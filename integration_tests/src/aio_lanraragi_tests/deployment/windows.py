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
from aio_lanraragi_tests.common import is_port_available
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.common import DEFAULT_API_KEY

LOGGER = logging.getLogger(__name__)

class WindowsLRRDeploymentContext(AbstractLRRDeploymentContext):
    """
    Set up a LANraragi environment on Windows. Requires a win-dist path to be provided.
    """

    @property
    def contents_dir(self) -> Path:
        """
        Absolute path to the entire LRR application. For testing purposes,
        contents_dir is determined by windist path + resource prefix.

        At the end of testing, the contents_dir should be removed.
        """
        contents_dirname = self.resource_prefix + "contents"
        return self.windist_path / contents_dirname
    
    @property
    def redis_dir(self) -> Path:
        """
        Absolute path to the Redis application (according to runfile, is same as contents dir)
        """
        return self.contents_dir

    @property
    def thumb_dir(self) -> Path:
        """
        Absolute path to the LRR thumbnail directory
        """
        return self.contents_dir / "thumb"

    @property
    def logs_dir(self) -> Path:
        return self.contents_dir / "log"
    
    @property
    def temp_dir(self) -> Path:
        return self.contents_dir / "temp"

    @property
    def lrr_log_path(self) -> Path:
        return self.logs_dir / "lanraragi.log"
    
    @property
    def redis_log_path(self) -> Path:
        return self.logs_dir / "redis.log"
    
    @property
    def lrr_address(self) -> str:
        """
        Address of the LRR server (i.e. http://127.0.0.1:$port)
        """
        return f"http://127.0.0.1:{self.lrr_port}"

    @property
    def windist_path(self) -> Path:
        """
        Absolute path to the LRR distribution directory containing the runfile.
        """
        return self._windist_path
    
    @windist_path.setter
    def windist_path(self, path: Path):
        self._windist_path = path.absolute()

    @property
    def redis_client(self) -> redis.Redis:
        """
        Redis client for this LRR deployment
        """
        if not hasattr(self, "_redis_client") or not self._redis_client:
            self._redis_client = redis.Redis(host="127.0.0.1", port=self.redis_port, decode_responses=True)
        return self._redis_client
    
    @redis_client.setter
    def redis_client(self, client: redis.Redis):
        self._redis_client = client

    def __init__(
        self, windist_path: str, resource_prefix: str, port_offset: int,
        logger: Optional[logging.Logger]=None
    ):
        self.resource_prefix = resource_prefix
        self.port_offset = port_offset

        self.windist_path = Path(windist_path)

        if logger is None:
            logger = LOGGER
        self.logger = logger

    @override
    def update_api_key(self, api_key: Optional[str]):
        self.redis_client.select(2)
        if api_key is None:
            self.redis_client.hdel("LRR_CONFIG", "apikey")
        else:
            self.redis_client.hset("LRR_CONFIG", "apikey", api_key)

    @override
    def enable_nofun_mode(self):
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "nofunmode", "1")

    @override
    def disable_nofun_mode(self):
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "nofunmode", "0")

    @override
    def enable_lrr_debug_mode(self):
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "enable_devmode", "1")

    @override
    def disable_lrr_debug_mode(self):
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "enable_devmode", "0")

    @override
    def setup(
        self, with_api_key: bool=False, with_nofunmode: bool=False, lrr_debug_mode: bool=False,
        test_connection_max_retries: int=4
    ):
        """
        Setup the LANraragi environment.

        Teardowns do not necessarily guarantee port availability. Windows may
        keep a port non-bindable for a short period of time even with no visible owning process.

        This setup logic is adapted from the LRR runfile, except we will start redis
        and LRR individually, and inject configuration data between redis/LRR startups
        to avoid having to restart LRR.
        """
        lrr_port = self.lrr_port
        redis_port = self.redis_port
        windist_path = self.windist_path
        if not windist_path.exists():
            raise FileNotFoundError(f"win-dist path {windist_path} not found.")

        # log the setup resource allocations for user to see
        self.logger.info(f"Deploying Windows LRR with the following resources: LRR port {lrr_port}, Redis port {redis_port}, content path {self.contents_dir}.")

        contents_dir = self.contents_dir
        thumb_dir = self.thumb_dir
        if contents_dir.exists():
            self.logger.info(f"Contents directory exists: {contents_dir}")
        else:
            self.logger.info(f"Creating contents dir: {contents_dir}")
            contents_dir.mkdir(parents=True, exist_ok=False)
        if thumb_dir.exists():
            self.logger.info(f"Thumb directory exists: {thumb_dir}")
        else:
            self.logger.info(f"Creating thumb directory: {thumb_dir}")
            thumb_dir.mkdir(parents=True, exist_ok=False)

        # we need to handle cases where existing services are running.
        # Unlike docker, we have no idea whether we can skip recreation of
        # the LRR process, so we will always recreate it.
        if is_port_available(redis_port):
            self.start_redis()
            self._test_redis_connection()
            self.logger.info(f"Redis service is established on port {redis_port}.")
        else:
            # TODO: this throws an exception if not redis on port or redis broken
            self._test_redis_connection()
            self.logger.info(f"Running Redis service confirmed on port {redis_port}, skipping startup.")
        if with_api_key:
            self.update_api_key(DEFAULT_API_KEY)
        if with_nofunmode:
            self.enable_nofun_mode()
        if lrr_debug_mode:
            self.enable_lrr_debug_mode()
        self.logger.info("Redis post-connect configuration complete.")

        if is_port_available(lrr_port):
            self.start_lrr()
            self.test_lrr_connection()
            self.logger.info(f"LRR service is established on port {lrr_port}.")
        else:
            self.logger.info(f"Found running LRR service on port {lrr_port}. Restarting...")
            self.stop_lrr()
            self.start_lrr()
            self.logger.info("LRR service restarted.")

        # TODO: do we need this?
        self.redis_pid = self._get_redis_pid()
        self.lrr_pid = self._get_lrr_pid()

        self.logger.info(f"Completed setup of LANraragi. LRR PID = {self.lrr_pid}; Redis PID = {self.redis_pid}.")

    @override
    def start(self, test_connection_max_retries: int = 4):
        """
        Start LRR and Redis on Windows via runfile.

        Unlike setup stage, if either services are running we won't do a restart,
        similar to the docker compose behavior.
        """
        redis_port = self.redis_port
        if is_port_available(redis_port):
            self.start_redis()
            self._test_redis_connection()
            self.logger.info(f"Redis service is established on port {redis_port}.")
        else:
            # TODO: this throws an exception if not redis on port or redis broken
            self._test_redis_connection()
            self.logger.info(f"Running Redis service confirmed on port {redis_port}, skipping startup.")
        self.logger.info("Started Redis.")

        lrr_port = self.lrr_port
        if is_port_available(lrr_port):
            self.start_lrr()
            self.test_lrr_connection()
            self.logger.info(f"LRR service established on port {lrr_port}")
        else:
            self.test_lrr_connection()
            self.logger.info(f"Running LRR service confirmed on port {lrr_port}, skipping startup.")

    @override
    def stop(self):
        self.stop_lrr()
        self.logger.info("Stopped LRR.")
        self.stop_redis()
        self.logger.info("Stopped Redis.")

    @override
    def restart(self):
        self.stop()
        self.start()

    @override
    def teardown(self, remove_data: bool=False):
        """
        Forceful shutdown of LRR and Redis and remove the content path, preparing it for another test.
        """
        contents_dir = self.contents_dir
        self.stop()

        if contents_dir.exists() and remove_data:
            shutil.rmtree(contents_dir)
            self.logger.info(f"Removed contents directory: {contents_dir}")

    @override
    def start_lrr(self):
        """
        Executes the LRR portion of tools/build/windows/run.ps1.
        """
        cwd = os.getcwd()

        try:
            windist_path = self.windist_path
            if not windist_path.exists():
                raise DeploymentException(f"Expected windist {windist_path} to exist.")
            os.chdir(windist_path)

            lrr_network = self.lrr_address
            lrr_data_directory = self.contents_dir
            lrr_log_directory = self.logs_dir
            lrr_temp_directory = self.temp_dir
            lrr_thumb_directory = self.thumb_dir
            if not lrr_log_directory.exists():
                self.logger.info(f"Making logs directory: {lrr_log_directory}")
                lrr_log_directory.mkdir(parents=True, exist_ok=False)
            else:
                self.logger.info(f"Logs directory exists: {lrr_log_directory}")
            if not lrr_temp_directory.exists():
                self.logger.info(f"Making temp directory: {lrr_temp_directory}")
                lrr_temp_directory.mkdir(parents=True, exist_ok=False)
            else:
                self.logger.info(f"Temp directory exists: {lrr_temp_directory}")
            if not lrr_thumb_directory.exists():
                self.logger.info(f"Making thumb directory: {lrr_thumb_directory}")
                lrr_thumb_directory.mkdir(parents=True, exist_ok=False)
            else:
                self.logger.info(f"Thumb directory exists: {lrr_thumb_directory}")

            lrr_env = os.environ.copy()
            perl_path = str(windist_path / "runtime" / "bin" / "perl.exe")
            path_var = lrr_env.get("Path", lrr_env.get("PATH", ""))
            runtime_bin = str(windist_path / "runtime" / "bin")
            runtime_redis = str(windist_path / "runtime" / "redis")
            lrr_env["LRR_NETWORK"] = lrr_network
            lrr_env["LRR_DATA_DIRECTORY"] = str(lrr_data_directory)
            lrr_env["LRR_LOG_DIRECTORY"] = str(lrr_log_directory)
            lrr_env["LRR_TEMP_DIRECTORY"] = str(lrr_temp_directory)
            lrr_env["LRR_THUMB_DIRECTORY"] = str(lrr_thumb_directory)
            lrr_env["LRR_REDIS_ADDRESS"] = f"127.0.0.1:{self.redis_port}"
            lrr_env["Path"] = runtime_bin + os.pathsep + runtime_redis + os.pathsep + path_var if path_var else runtime_bin + os.pathsep + runtime_redis

            script = [
                perl_path, str(Path("script") / "launcher.pl"),
                "-d", str(Path("script") / "lanraragi")
            ]
            self.logger.info(f"(lrr_network={lrr_network}, lrr_data_directory={lrr_data_directory}, lrr_log_directory={lrr_log_directory}, lrr_temp_directory={lrr_temp_directory}, lrr_thumb_directory={lrr_thumb_directory}) running script {subprocess.list2cmdline(script)}")
            lrr_process = subprocess.Popen(script, env=lrr_env)
            self.logger.info(f"Started LRR process with PID: {lrr_process.pid}.")
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
            if not windist_path.exists():
                raise DeploymentException(f"Expected windist {windist_path} to exist.")
            os.chdir(windist_path)

            logs_dir = self.logs_dir
            redis_server_path = str(windist_path / "runtime" / "redis" / "redis-server.exe")
            pid_filepath = logs_dir / "redis.pid"
            redis_dir = self.redis_dir
            redis_logfile_path = self.redis_log_path
            contents_dir = self.contents_dir

            if not logs_dir.exists():
                self.logger.info(f"Creating logs directory: {logs_dir}")
                logs_dir.mkdir(parents=True, exist_ok=False)
            if not contents_dir.exists():
                self.logger.info(f"Creating contents directory: {contents_dir}")
                contents_dir.mkdir(parents=True, exist_ok=False)

            script = [
                redis_server_path, str(Path("runtime") / "redis" / "redis.conf"),
                "--pidfile", str(pid_filepath), # maybe we don't need this...?
                "--dir", str(redis_dir),
                "--logfile", str(redis_logfile_path),
                "--port", str(self.redis_port),
            ]
            self.logger.info(f"(redis_dir={redis_dir}, redis_logfile_path={redis_logfile_path}) running script {subprocess.list2cmdline(script)}")
            redis_process = subprocess.Popen(script)
            self.logger.info(f"Started redis service with PID {redis_process.pid}.")
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
        lrr_port = self.lrr_port
        
        if pid:
            self.logger.info(f"LRR PID {pid} found; killing...")
            output = subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"])
            if output.returncode != 0:
                self.logger.error(f"LRR PID {pid} shutdown failed with exit code {output.returncode}")
            else:
                self.logger.debug(f"LRR PID {pid} shutdown output: {output.stdout}")
        else:
            self.logger.warning("No LRR PID found; attempting to free port and kill matching processes.")
            owner_pid = self._get_port_owner_pid(lrr_port)
            if owner_pid:
                self.logger.info(f"Killing process {owner_pid} for LRR port {lrr_port}...")
                subprocess.run(["taskkill", "/PID", str(owner_pid), "/F", "/T"])  # best-effort
            self.logger.info("Killing perl.exe processes by path...")
            self._kill_lrr_perl_processes_by_path()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get_lrr_pid() is None and is_port_available(lrr_port):
                self.logger.debug("LRR shutdown complete...?")
                # could be a hoax, let's check again
                time.sleep(1.0)
                if self._get_lrr_pid() is None and is_port_available(lrr_port):
                    self.logger.info("LRR shutdown complete.")
                    return
                else:
                    self.logger.warning(f"Nope, ANOTHER process seems to be owning LRR port {lrr_port} now.")
                    continue
            new_owner = self._get_port_owner_pid(lrr_port)
            if new_owner:
                self.logger.info(f"Yet another process seems to be owning LRR port {lrr_port}; killing...")
                subprocess.run(["taskkill", "/PID", str(new_owner), "/F", "/T"])
            time.sleep(0.2)

        self._ensure_port_free(lrr_port, service_name="LRR", timeout=60)
        if is_port_available(lrr_port):
            self.logger.info("LRR shutdown complete after extended wait.")
        else:
            self.logger.warning(f"Wow! LRR port {lrr_port} STILL. WON'T. LET. GO.")
        raise DeploymentException(f"LRR is still running or port {lrr_port} still occupied after {timeout}s.")

    @override
    def stop_redis(self, timeout: int = 10):
        self.redis_client.shutdown(now=True, force=True)

    @override
    def get_lrr_logs(self, tail: int=100) -> bytes:
        if self.lrr_log_path.exists():
            with open(self.lrr_log_path, 'rb') as rb:
                lines = rb.readlines()
                # Normalize Windows CRLF line endings to LF to avoid extra spacing
                normalized_lines = [line.replace(b'\r\n', b'\n') for line in lines]
                return b''.join(normalized_lines[-tail:])
        return b"No LRR logs available."

    def _get_lrr_pid(self) -> Optional[int]:
        # Windows run script starts server in daemon mode,
        # which don't create server.pid. We will get
        # the PID by the process that owns the listening port.
        return self._get_port_owner_pid(self.lrr_port)

    def _get_redis_pid(self) -> Optional[int]:
        # see _get_lrr_pid for explanation
        return self._get_port_owner_pid(self.redis_port)

    def _test_redis_connection(self, max_retries: int=4):
        self.logger.debug("Connecting to Redis...")
        retry_count = 0
        while True:
            try:
                self.redis_client.ping()
                break
            except redis.exceptions.ConnectionError:
                if retry_count >= max_retries:
                    raise
                time_to_sleep = 2 ** (retry_count + 1)
                self.logger.warning(f"Failed to connect to Redis. Retry in {time_to_sleep}s ({retry_count+1}/{max_retries})...")
                retry_count += 1
                time.sleep(time_to_sleep)

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
            self.logger.debug(f"Killing perl process PID {p}...")
            subprocess.run(["taskkill", "/PID", p, "/F", "/T"])
        self.logger.debug("Killing perl processes by path attempt complete.")

    def _ensure_port_free(self, port: int, service_name: str, timeout: int = 15):
        """
        Ensure the given port is free by attempting to kill the owning process tree and waiting.
        If port is a suspected LRR port, also kill perl.exe processes by path.
        """
        self.logger.info(f"Ensuring port {port} is free for {service_name}...")
        if is_port_available(port):
            self.logger.debug(f"{service_name} port {port} is already free.")
            return
        owner_pid = self._get_port_owner_pid(port)
        if owner_pid:
            self.logger.debug(f"Killing process {owner_pid} for {service_name} port {port}...")
            subprocess.run(["taskkill", "/PID", str(owner_pid), "/F", "/T"])
        if port == self.lrr_port and not is_port_available(port):
            self.logger.warning(f"LRR port {port} still occupied after best-effort cleanup; killing perl.exe processes by path.")
            self._kill_lrr_perl_processes_by_path()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_port_available(port):
                self.logger.debug(f"{service_name} port {port} is now free...?")
                time.sleep(1.0)
                if is_port_available(port):
                    self.logger.info(f"{service_name} port {port} is now free.")
                    return
                else:
                    self.logger.warning(f"Oh you sneaky snake... ANOTHER process seems to be owning {service_name} port {port} now.")
                    continue
            time.sleep(0.2)
        raise DeploymentException(f"{service_name} port {port} is still occupied after {timeout}s.")