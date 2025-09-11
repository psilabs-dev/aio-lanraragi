import io
import logging
from pathlib import Path
import redis
import shutil
import subprocess
import threading
import time
from typing import Optional, override

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import is_port_available
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.common import DEFAULT_API_KEY

LOGGER = logging.getLogger(__name__)


class WindowsLRRDeploymentContext(AbstractLRRDeploymentContext):
    """
    Set up a LANraragi environment on Windows. Requires a runfile to be provided.
    """

    def __init__(
        self, runfile: str, content_path: str,
        logger: Optional[logging.Logger]=None,
        lrr_port: int=3001
    ):
        self.runfile = Path(runfile)
        if logger is None:
            logger = LOGGER
        self.logger = logger
        self.lrr_port = lrr_port
        self.redis_port = 6379 # maybe this can be changed?

        self.network = f"http://127.0.0.1:{lrr_port}"
        self.content_path = Path(content_path).absolute()
        self.thumb_path = self.content_path / "thumb"
        # log and pid files are written under the dist directory where run.ps1 is executed
        self.logsdir = self.runfile.parent / "log"
        self.lrr_logs_path = self.logsdir / "lanraragi.log"

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
        self, with_api_key: bool=False, with_nofunmode: bool=False,
        test_connection_max_retries: int=4
    ):
        """
        Setup the LANraragi environment.

        Teardowns do not necessarily guarantee port availability. Windows may
        keep a port non-bindable for a short period of time even with no visible owning process.
        """
        runfile = self.runfile.absolute()
        if not runfile.exists():
            raise FileNotFoundError(f"Runfile {runfile} not found.")

        self.get_logger().info("Checking if ports are available.")
        if not is_port_available(self.lrr_port):
            self.get_logger().warning(f"LRR port {self.lrr_port} is occupied; attempting to free it.")
            self._ensure_port_free(self.lrr_port, service_name="LRR", timeout=15)
            if not is_port_available(self.lrr_port):
                # What now.
                raise DeploymentException(f"Port {self.lrr_port} is occupied.")
        if not is_port_available(self.redis_port):
            raise DeploymentException(f"Redis port {self.redis_port} is occupied.")
        self.get_logger().info("Creating required directories...")
        self.content_path.mkdir(parents=True, exist_ok=True)
        self.thumb_path.mkdir(parents=True, exist_ok=True)

        self._execute_lrr_runfile()
        
        # post LRR startup
        self.get_logger().info("Setup script execution complete; testing connection to LRR server.")
        self.test_lrr_connection(test_connection_max_retries)

        # connect to redis
        self._test_redis_connection()
        restart_required = False
        if with_api_key:
            self.get_logger().info("Adding API key to Redis...")
            self.update_api_key(DEFAULT_API_KEY)
            restart_required = True
        if with_nofunmode:
            self.get_logger().info("Enabling NoFun mode...")
            self.enable_nofun_mode()
            restart_required = True

        if restart_required:
            self._restart_deployment(test_connection_max_retries)

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

        if self.content_path.exists() and remove_data:
            self.get_logger().info(f"Removing content path: {self.content_path}")
            shutil.rmtree(self.content_path)

    @override
    def restart(self):
        self._restart_deployment()

    @override
    def start_lrr(self):
        raise NotImplementedError

    @override
    def start_redis(self):
        raise NotImplementedError

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
        if pid:
            self.get_logger().info(f"LRR PID {pid} found; killing...")
            output = subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"])
            if output.returncode != 0:
                self.get_logger().error(f"LRR PID {pid} shutdown failed with exit code {output.returncode}")
            else:
                self.get_logger().debug(f"LRR PID {pid} shutdown output: {output.stdout}")
        else:
            self.get_logger().warning("No LRR PID found; attempting to free port and kill matching processes.")
            owner_pid = self._get_port_owner_pid(self.lrr_port)
            if owner_pid:
                self.get_logger().info(f"Killing process {owner_pid} for LRR port {self.lrr_port}...")
                subprocess.run(["taskkill", "/PID", str(owner_pid), "/F", "/T"])  # best-effort
            self.get_logger().info("Killing perl.exe processes by path...")
            self._kill_lrr_perl_processes_by_path()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get_lrr_pid() is None and is_port_available(self.lrr_port):
                self.get_logger().debug("LRR shutdown complete...?")
                # could be a hoax, let's check again
                time.sleep(1.0)
                if self._get_lrr_pid() is None and is_port_available(self.lrr_port):
                    self.get_logger().info("LRR shutdown complete.")
                    return
                else:
                    self.get_logger().warning(f"Nope, ANOTHER process seems to be owning LRR port {self.lrr_port} now.")
                    continue
            new_owner = self._get_port_owner_pid(self.lrr_port)
            if new_owner:
                self.get_logger().info(f"Yet another process seems to be owning LRR port {self.lrr_port}; killing...")
                subprocess.run(["taskkill", "/PID", str(new_owner), "/F", "/T"])
            time.sleep(0.2)

        self._ensure_port_free(self.lrr_port, service_name="LRR", timeout=60)
        if is_port_available(self.lrr_port):
            self.get_logger().info("LRR shutdown complete after extended wait.")
        else:
            self.get_logger().warning(f"Wow! LRR port {self.lrr_port} STILL. WON'T. LET. GO.")
        raise DeploymentException(f"LRR is still running or port {self.lrr_port} still occupied after {timeout}s.")

    @override
    def stop_redis(self, timeout: int = 10):
        pid = self._get_redis_pid()
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
            if self._get_redis_pid() is None and is_port_available(self.redis_port):
                self.get_logger().debug(f"Redis PID {pid} shutdown complete.")
                return

            time.sleep(0.2)
        raise DeploymentException(f"Redis PID {pid} is still running after {timeout}s, forcing shutdown.")

    @override
    def get_lrr_logs(self, tail: int=100) -> bytes:
        if self.lrr_logs_path.exists():
            with open(self.lrr_logs_path, 'rb') as rb:
                lines = rb.readlines()
                return b''.join(lines[-tail:])
        return b"No LRR logs available."

    def _execute_lrr_runfile(self):
        runfile = self.runfile.absolute()
        if not runfile.exists():
            raise FileNotFoundError(f"Runfile {runfile} not found.")

        self.get_logger().debug("Building script.")
        script = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(runfile),
            "-Data", str(self.content_path),
            "-Thumb", str(self.thumb_path),
            "-Network", self.network,
            "-Database", str(self.content_path)
        ]
        script_s = subprocess.list2cmdline(script)
        self.get_logger().info(f"Preparing to run script: {script_s}")

        process = subprocess.Popen(
            script,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            cwd=str(self.runfile.parent)
        )

        captured_lines = []
        def log_stream(stream: io.TextIOWrapper):
            for line in iter(stream.readline, ''):
                line = line.rstrip("\r\n")
                if line:
                    captured_lines.append(line)
                    self.get_logger().info(line)
            stream.close()
        t = threading.Thread(target=log_stream, args=(process.stdout,), daemon=True)
        t.start()

        try:
            exit_code = process.wait()
        except KeyboardInterrupt:
            process.kill()
            process.wait()
            raise
        finally:
            t.join()
        
        if exit_code != 0:
            output = "\n".join(captured_lines[-200:])
            raise subprocess.CalledProcessError(
                returncode=exit_code,
                cmd=script,
                output=output
            )

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
        return self._get_port_owner_pid(self.lrr_port)

    def _get_redis_pid(self) -> Optional[int]:
        # see _get_lrr_pid for explanation
        return self._get_port_owner_pid(self.redis_port)

    def _test_redis_connection(self):
        self.get_logger().debug("Connecting to Redis...")
        if not self.redis:
            self.redis = redis.Redis(host="127.0.0.1", port=self.redis_port, decode_responses=True)
        self.redis.ping()
        self.get_logger().info("Redis connection established.")

    def _restart_deployment(self, test_connection_max_retries: int=4):
        self.get_logger().info("Restart require detected; restarting LRR and Redis...")
        self.stop_lrr()
        self.stop_redis()
        self._execute_lrr_runfile()
        self.get_logger().debug("Testing connection to LRR server.")
        self.test_lrr_connection(test_connection_max_retries)
        self._test_redis_connection()
        self.get_logger().info("Restart complete.")

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
        Kill perl.exe processes started from the win-dist runtime path for this runfile directory.
        """
        perl_path = str((self.runfile.parent / "runtime" / "bin" / "perl.exe").absolute())
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
        """
        self.get_logger().info(f"Ensuring port {port} is free for {service_name}...")
        if is_port_available(port):
            self.get_logger().debug(f"{service_name} port {port} is already free.")
            return
        owner_pid = self._get_port_owner_pid(port)
        if owner_pid:
            self.get_logger().debug(f"Killing process {owner_pid} for {service_name} port {port}...")
            subprocess.run(["taskkill", "/PID", str(owner_pid), "/F", "/T"])
        if port == self.lrr_port and not is_port_available(port):
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