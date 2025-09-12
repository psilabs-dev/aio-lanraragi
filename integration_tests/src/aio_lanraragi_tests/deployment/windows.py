"""
Python module for setting up and tearing down Windows environments for LANraragi.
"""

import logging
import os
from pathlib import Path
import redis
import redis.exceptions
import shutil
import subprocess
import time
from typing import Optional, override

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import is_port_available, DEFAULT_LRR_PORT, DEFAULT_REDIS_PORT
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.common import DEFAULT_API_KEY

LOGGER = logging.getLogger(__name__)

class WindowsLRRDeploymentContext(AbstractLRRDeploymentContext):
    """
    Set up a LANraragi environment on Windows. Requires a win-dist path to be provided.
    """

    def __init__(
        self, windist_path: str,
        logger: Optional[logging.Logger]=None
    ):
        self.windist_path = Path(windist_path)
        if logger is None:
            logger = LOGGER
        self.logger = logger

        self.redis_pid: Optional[int] = None
        self.lrr_pid: Optional[int] = None
        self.redis: Optional[redis.Redis] = None


    @override
    def get_logger(self) -> logging.Logger:
        return self.logger

    @override
    def update_api_key(self, api_key: Optional[str]):
        self.redis.select(2)
        if api_key is None:
            self.redis.hdel("LRR_CONFIG", "apikey")
        else:
            self.redis.hset("LRR_CONFIG", "apikey", api_key)

    @override
    def enable_nofun_mode(self):
        self.redis.select(2)
        self.redis.hset("LRR_CONFIG", "nofunmode", "1")

    @override
    def disable_nofun_mode(self):
        self.redis.select(2)
        self.redis.hset("LRR_CONFIG", "nofunmode", "0")

    @override
    def setup(
        self, resource_prefix: str, port_offset: int,
        with_api_key: bool=False, with_nofunmode: bool=False,
        test_connection_max_retries: int=4
    ):
        """
        Setup the LANraragi environment.

        Teardowns do not necessarily guarantee port availability. Windows may
        keep a port non-bindable for a short period of time even with no visible owning process.
        """
        self.resource_prefix = resource_prefix
        self.port_offset = port_offset
        lrr_port = self.get_lrr_port()
        redis_port = self._get_redis_port()

        windist_path = self.windist_path.absolute()
        if not windist_path.exists():
            raise FileNotFoundError(f"win-dist path {windist_path} not found.")

        self.get_logger().info("Checking if ports are available.")
        if not is_port_available(lrr_port):
            self.get_logger().warning(f"LRR port {lrr_port} is occupied; attempting to free it.")
            self._ensure_port_free(lrr_port, service_name="LRR", timeout=15)
            if not is_port_available(lrr_port):
                # What now.
                raise DeploymentException(f"Port {lrr_port} is occupied.")
        if not is_port_available(redis_port):
            raise DeploymentException(f"Redis port {redis_port} is occupied.")
        self.get_logger().info("Creating required directories...")
        self._get_contents_path().mkdir(parents=True, exist_ok=True)
        self._get_thumb_path().mkdir(parents=True, exist_ok=True)

        # self._execute_lrr_runfile()
        self.start_redis()
        # self._test_redis_connection()
        if with_api_key:
            self.get_logger().info("Adding API key to Redis...")
            self.update_api_key(DEFAULT_API_KEY)
        if with_nofunmode:
            self.get_logger().info("Enabling NoFun mode...")
            self.enable_nofun_mode()

        # post LRR startup; if we start redis and LRR independently, 
        # then there's no need to restart the server.
        self.start_lrr()
        self.get_logger().debug("Collecting PID info...")
        self.redis_pid = self._get_redis_pid()
        self.lrr_pid = self._get_lrr_pid()

        self.get_logger().info(f"Completed setup of LANraragi. LRR PID = {self.lrr_pid}; Redis PID = {self.redis_pid}.")

    @override
    def teardown(self, remove_data: bool=False):
        """
        Forceful shutdown of LRR and Redis and remove the content path, preparing it for another test.
        """
        self.stop_lrr()
        self.stop_redis()
        contents_path = self._get_contents_path()
        windist_logs_path = self.windist_path / "log"
        windist_temp_path = self._get_lrr_temp_directory()
        if contents_path.exists() and remove_data:
            self.get_logger().info(f"Removing content path: {contents_path}")
            shutil.rmtree(contents_path)
        if windist_logs_path.exists() and remove_data:
            self.get_logger().info(f"Removing windist logs path: {windist_logs_path}")
            shutil.rmtree(windist_logs_path)
        if windist_temp_path.exists() and remove_data:
            self.get_logger().info(f"Removing windist temp path: {windist_temp_path}")
            shutil.rmtree(windist_temp_path)

    @override
    def restart(self):
        self.get_logger().info("Restart require detected; restarting LRR and Redis...")
        self.stop_lrr()
        self.stop_redis()
        self.start_redis()
        self.start_lrr()
        self.get_logger().info("Restart complete.")

    @override
    def start_lrr(self):
        """
        Executes the LRR portion of tools/build/windows/run.ps1.
        """
        cwd = os.getcwd()

        try:
            windist_path = self.windist_path.absolute()
            logs_dir = self._get_logs_dir()
            temp_dir = self._get_lrr_temp_directory()
            thumb_dir = self._get_thumb_path()
            redis_port = self._get_redis_port()
            if not windist_path.exists():
                raise DeploymentException(f"Expected windist {windist_path} to exist.")
            os.chdir(windist_path)

            if not logs_dir.exists():
                self.get_logger().info(f"Logs directory {logs_dir} does not exist; making...")
                logs_dir.mkdir(parents=True, exist_ok=False)
            if not temp_dir.exists():
                self.get_logger().info(f"Temp directory {temp_dir} does not exist; making...")
                temp_dir.mkdir(parents=True, exist_ok=False)

            lrr_env = os.environ.copy()
            perl_path = str(windist_path / "runtime" / "bin" / "perl.exe")
            path_var = lrr_env.get("Path", lrr_env.get("PATH", ""))
            runtime_bin = str(windist_path / "runtime" / "bin")
            runtime_redis = str(windist_path / "runtime" / "redis")
            lrr_network = self._get_lrr_network()
            lrr_data_directory = self._get_contents_path()
            lrr_log_directory = logs_dir
            lrr_temp_directory = temp_dir
            lrr_thumb_directory = thumb_dir
            lrr_env["LRR_NETWORK"] = lrr_network
            lrr_env["LRR_DATA_DIRECTORY"] = str(lrr_data_directory)
            lrr_env["LRR_LOG_DIRECTORY"] = str(lrr_log_directory)
            lrr_env["LRR_TEMP_DIRECTORY"] = str(lrr_temp_directory)
            lrr_env["LRR_THUMB_DIRECTORY"] = str(lrr_thumb_directory)
            lrr_env["LRR_REDIS_ADDRESS"] = f"127.0.0.1:{redis_port}"
            lrr_env["Path"] = runtime_bin + os.pathsep + runtime_redis + os.pathsep + path_var if path_var else runtime_bin + os.pathsep + runtime_redis

            script = [
                perl_path, str(Path("script") / "launcher.pl"),
                "-d", str(Path("script") / "lanraragi")
            ]
            self.get_logger().info(f"(lrr_network={lrr_network}, lrr_data_directory={lrr_data_directory}, lrr_log_directory={lrr_log_directory}, lrr_temp_directory={lrr_temp_directory}, lrr_thumb_directory={lrr_thumb_directory}) running script {subprocess.list2cmdline(script)}")
            lrr_process = subprocess.Popen(script, env=lrr_env)
            self.get_logger().info(f"Started LRR process with PID {lrr_process.pid}.")
            # confirm it has started.
            self.test_lrr_connection(self.get_lrr_port())
        finally:
            os.chdir(cwd)

    @override
    def start_redis(self):
        """
        Executes the Redis portion of tools/build/windows/run.ps1.
        """
        cwd = os.getcwd()

        try:
            windist_path = self.windist_path.absolute()
            temp_path =  self._get_lrr_temp_directory()
            logs_dir =  self._get_logs_dir()
            if not windist_path.exists():
                raise DeploymentException(f"Expected windist {windist_path} to exist.")
            os.chdir(windist_path)

            if not logs_dir.exists():
                self.get_logger().info(f"Logs directory {logs_dir} does not exist; making...")
                logs_dir.mkdir(parents=True, exist_ok=False)
            if not temp_path.exists():
                self.get_logger().info(f"Temp directory {temp_path} does not exist; making...")
                temp_path.mkdir(parents=True, exist_ok=False)

            redis_server_path = str(windist_path / "runtime" / "redis" / "redis-server.exe")
            pid_filepath = str(temp_path / "redis.pid")
            redis_dir = self._get_contents_path()
            redis_logfile_path = self._get_redis_logs_path()
            script = [
                redis_server_path, str(Path("runtime") / "redis" / "redis.conf"),
                "--pidfile", str(pid_filepath), # maybe we don't need this...?
                "--dir", str(redis_dir),
                "--logfile", str(redis_logfile_path),
                "--port", str(self._get_redis_port()),
            ]
            self.get_logger().info(f"(redis_dir={redis_dir}, redis_logfile_path={redis_logfile_path}) running script {subprocess.list2cmdline(script)}")
            redis_process = subprocess.Popen(script)
            self.get_logger().info(f"Started redis service with PID {redis_process.pid}.")
            # confirm it has started.
            self._test_redis_connection()
        finally:
            os.chdir(cwd)

    @override
    def stop_lrr(self, timeout: int = 60):
        """
        Stop the LRR server.

        This will try to kill the LRR server by PID, then by port owner PID, 
        then by perl.exe processes started from our win-dist runtime.

        lrr_pid being None only means PID probe didn't find a listening owner
        at that instant, does not guarantee that the port is bindable.

        Taskkill only returns when we found a PID, if PID lookup fails, skips
        kill and doesn't wait for port to be clear.
        """
        pid = self._get_lrr_pid()
        lrr_port = self.get_lrr_port()
        
        if pid:
            self.get_logger().info(f"LRR PID {pid} found; killing...")
            output = subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"])
            if output.returncode != 0:
                self.get_logger().error(f"LRR PID {pid} shutdown failed with exit code {output.returncode}")
            else:
                self.get_logger().debug(f"LRR PID {pid} shutdown output: {output.stdout}")
        else:
            self.get_logger().warning("No LRR PID found; attempting to free port and kill matching processes.")
            owner_pid = self._get_port_owner_pid(lrr_port)
            if owner_pid:
                self.get_logger().info(f"Killing process {owner_pid} for LRR port {lrr_port}...")
                subprocess.run(["taskkill", "/PID", str(owner_pid), "/F", "/T"])  # best-effort
            self.get_logger().info("Killing perl.exe processes by path...")
            self._kill_lrr_perl_processes_by_path()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get_lrr_pid() is None and is_port_available(lrr_port):
                self.get_logger().debug("LRR shutdown complete...?")
                # could be a hoax, let's check again
                time.sleep(1.0)
                if self._get_lrr_pid() is None and is_port_available(lrr_port):
                    self.get_logger().info("LRR shutdown complete.")
                    return
                else:
                    self.get_logger().warning(f"Nope, ANOTHER process seems to be owning LRR port {lrr_port} now.")
                    continue
            new_owner = self._get_port_owner_pid(lrr_port)
            if new_owner:
                self.get_logger().info(f"Yet another process seems to be owning LRR port {lrr_port}; killing...")
                subprocess.run(["taskkill", "/PID", str(new_owner), "/F", "/T"])
            time.sleep(0.2)

        self._ensure_port_free(lrr_port, service_name="LRR", timeout=60)
        if is_port_available(lrr_port):
            self.get_logger().info("LRR shutdown complete after extended wait.")
        else:
            self.get_logger().warning(f"Wow! LRR port {lrr_port} STILL. WON'T. LET. GO.")
        raise DeploymentException(f"LRR is still running or port {lrr_port} still occupied after {timeout}s.")

    @override
    def stop_redis(self, timeout: int = 10):
        pid = self._get_redis_pid()
        redis_port = self._get_redis_port()

        if not pid:
            self.get_logger().info("No Redis PID found, skipping shutdown.")
            return

        output = subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"])
        if output.returncode != 0:
            self.get_logger().error(f"Redis PID {pid} shutdown failed with exit code {output.returncode}")
        else:
            self.get_logger().debug(f"Redis PID {pid} shutdown output: {output.stdout}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get_redis_pid() is None and is_port_available(redis_port):
                self.get_logger().debug(f"Redis PID {pid} shutdown complete.")
                return

            time.sleep(0.2)
        raise DeploymentException(f"Redis PID {pid} is still running after {timeout}s, forcing shutdown. Redis port {redis_port} is still occupied.")

    @override
    def get_lrr_logs(self, tail: int=100) -> bytes:
        if self._get_lrr_logs_path().exists():
            with open(self._get_lrr_logs_path(), 'rb') as rb:
                lines = rb.readlines()
                # Normalize Windows CRLF line endings to LF to avoid extra spacing
                normalized_lines = [line.replace(b'\r\n', b'\n') for line in lines]
                return b''.join(normalized_lines[-tail:])
        return b"No LRR logs available."

    def _get_contents_path_str(self) -> str:
        return f"{self.resource_prefix}content"

    def _get_contents_path(self) -> Path:
        # TODO: we (probably) need an absolute path here but maybe there's a better way
        windist_path = self.windist_path.absolute()
        return (windist_path / self._get_contents_path_str()).absolute()

    def _get_lrr_temp_directory(self) -> Path:
        return self.windist_path / "temp"

    def _get_thumb_path(self) -> Path:
        return self._get_contents_path() / "thumb"
    
    def _get_logs_dir(self) -> Path:
        return self.windist_path / "log"

    def _get_lrr_logs_path(self) -> Path:
        return self._get_logs_dir() / "lanraragi.log"
    
    def _get_redis_logs_path(self) -> Path:
        return self._get_logs_dir() / "redis.log"

    @override
    def get_lrr_port(self) -> int:
        return DEFAULT_LRR_PORT + self.port_offset

    def _get_redis_port(self) -> int:
        return DEFAULT_REDIS_PORT + self.port_offset
    
    def _get_lrr_network(self) -> str:
        return f"http://127.0.0.1:{self.get_lrr_port()}"

    # # for reference only.
    # def _execute_lrr_runfile(self):
    #     runfile = self.runfile.absolute()
    #     if not runfile.exists():
    #         raise FileNotFoundError(f"Runfile {runfile} not found.")

    #     self.get_logger().debug("Building script.")
    #     script = [
    #         "powershell.exe",
    #         "-NoProfile",
    #         "-ExecutionPolicy", "Bypass",
    #         "-File", str(runfile),
    #         "-Data", str(self._get_contents_path()),
    #         "-Thumb", str(self._get_thumb_path()),
    #         "-Network", self._get_lrr_network(),
    #         "-Database", str(self._get_contents_path()),
    #         "-RedisPort", str(self._get_redis_port())
    #     ]
    #     script_s = subprocess.list2cmdline(script)
    #     self.get_logger().info(f"Preparing to run script: {script_s}")

    #     process = subprocess.Popen(
    #         script,
    #         stdout=subprocess.PIPE,
    #         stderr=subprocess.STDOUT,
    #         text=True,
    #         bufsize=1,
    #         encoding="utf-8",
    #         cwd=str(self.runfile_parent.parent)
    #     )

    #     captured_lines = []
    #     def log_stream(stream: io.TextIOWrapper):
    #         for line in iter(stream.readline, ''):
    #             line = line.rstrip("\r\n")
    #             if line:
    #                 captured_lines.append(line)
    #                 self.get_logger().info(line)
    #         stream.close()
    #     t = threading.Thread(target=log_stream, args=(process.stdout,), daemon=True)
    #     t.start()

    #     try:
    #         exit_code = process.wait()
    #     except KeyboardInterrupt:
    #         process.kill()
    #         process.wait()
    #         raise
    #     finally:
    #         t.join()
        
    #     if exit_code != 0:
    #         output = "\n".join(captured_lines[-200:])
    #         raise subprocess.CalledProcessError(
    #             returncode=exit_code,
    #             cmd=script,
    #             output=output
    #         )

    def _is_running(self, pid: int) -> bool:
        """
        Get whether a process is running.
        """
        script = [
            "tasklist", "/FI", f"PID eq {pid}"
        ]
        return str(pid) in subprocess.run(
            script, capture_output=True, text=True
        ).stdout

    def _get_lrr_pid(self) -> Optional[int]:
        # Windows run script starts server in daemon mode,
        # which don't create server.pid. We will get
        # the PID by the process that owns the listening port.
        return self._get_port_owner_pid(self.get_lrr_port())

    def _get_redis_pid(self) -> Optional[int]:
        # see _get_lrr_pid for explanation
        return self._get_port_owner_pid(self._get_redis_port())

    def _test_redis_connection(self, max_retries: int=4):
        self.get_logger().debug("Connecting to Redis...")
        if not self.redis:
            self.redis = redis.Redis(host="127.0.0.1", port=self._get_redis_port(), decode_responses=True)

        retry_count = 0
        while True:
            try:
                self.redis.ping()
                break
            except redis.exceptions.ConnectionError:
                if retry_count >= max_retries:
                    raise
                time_to_sleep = 2 ** (retry_count + 1)
                self.get_logger().warning(f"Failed to connect to Redis. Retry in {time_to_sleep}s ({retry_count+1}/{max_retries})...")
                retry_count += 1
                time.sleep(time_to_sleep)
        self.get_logger().info("Redis connection established.")

    def _get_port_owner_pid(self, port: int) -> Optional[int]:
        cmd = f"Get-NetTCPConnection -LocalPort {port} | Select-Object -First 1 -ExpandProperty OwningProcess"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True
        )
        pid = result.stdout.strip()
        return int(pid) if pid.isdigit() else None

    def _kill_lrr_perl_processes_by_path(self):
        """
        Kill perl.exe processes started from within the win-dist runtime path.
        """
        perl_path = str((self.windist_path / "runtime" / "bin" / "perl.exe").absolute())
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name = 'perl.exe'\" | "
            f"Where-Object {{ $_.ExecutablePath -ieq '{perl_path}' }} | "
            "Select-Object -ExpandProperty ProcessId"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, text=True
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip().isdigit()]
        for p in pids:
            self.get_logger().debug(f"Killing perl process PID {p}...")
            subprocess.run(["taskkill", "/PID", p, "/F", "/T"])
        self.get_logger().debug("Killing perl processes by path attempt complete.")

    def _ensure_port_free(self, port: int, service_name: str, timeout: int = 15):
        """
        Ensure the given port is free by attempting to kill the owning process tree and waiting.
        If port is a suspected LRR port, also kill perl.exe processes by path.
        """
        self.get_logger().info(f"Ensuring port {port} is free for {service_name}...")
        if is_port_available(port):
            self.get_logger().debug(f"{service_name} port {port} is already free.")
            return
        owner_pid = self._get_port_owner_pid(port)
        if owner_pid:
            self.get_logger().debug(f"Killing process {owner_pid} for {service_name} port {port}...")
            subprocess.run(["taskkill", "/PID", str(owner_pid), "/F", "/T"])
        if port == self.get_lrr_port() and not is_port_available(port):
            self.get_logger().warning(f"LRR port {port} still occupied after best-effort cleanup; killing perl.exe processes by path.")
            self._kill_lrr_perl_processes_by_path()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_port_available(port):
                self.get_logger().debug(f"{service_name} port {port} is now free...?")
                time.sleep(1.0)
                if is_port_available(port):
                    self.get_logger().info(f"{service_name} port {port} is now free.")
                    return
                else:
                    self.get_logger().warning(f"Oh you sneaky snake... ANOTHER process seems to be owning {service_name} port {port} now.")
                    continue
            time.sleep(0.2)
        raise DeploymentException(f"{service_name} port {port} is still occupied after {timeout}s.")