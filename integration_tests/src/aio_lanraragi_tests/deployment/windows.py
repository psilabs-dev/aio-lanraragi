"""
Windows LRR deployment module.
"""

from contextlib import AbstractContextManager
import logging
import os
from pathlib import Path
import ctypes
import redis
import redis.exceptions
import shutil
import subprocess
import time
import stat
from typing import Optional, override

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import is_port_available
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.common import DEFAULT_API_KEY

LOGGER = logging.getLogger(__name__)

class _WindowsConsole(AbstractContextManager):
    """
    Context manager for windows console. Do not directly attach a process in scope, this will result in
    unaccounted-for orphaned attachments throughout the test runs.

    Within a `_WindowsConsoleContextManager` scope, the following are provided:
    - an attachment to a requested (or new) Windows console
    - immunity to CTRL events
    - ability to send CTRL signals to a target PID
    
    Should obviously not be run on non-Windows systems, and since this is private we will not be checking.
    """

    @property
    def attach_to_pid(self) -> Optional[int]:
        """
        PID (if any) whose console we plan to attach to.
        """
        return self._attach_to_pid
    
    @attach_to_pid.setter
    def attach_to_pid(self, pid: int):
        self._attach_to_pid = pid
    
    @property
    def had_console(self) -> bool:
        """
        True if current process already had a console before entering context.
        Used to know whether we must restore that original state on exit.
        """
        return self._had_console

    @had_console.setter
    def had_console(self, value: bool):
        self._had_console = value
    
    @property
    def detached_parent(self) -> bool:
        """
        True if we freed our console to make room for attaching to the target's console.
        On Windows, you must `FreeConsole` before `AttachConsole`.
        """
        return self._detached_parent

    @detached_parent.setter
    def detached_parent(self, value: bool):
        self._detached_parent = value
    
    @property
    def allocated_console(self) -> bool:
        """
        True if we allocated a brand-new console during `__enter__`.
        If so, `__exit__` will free it.
        """
        return self._allocated_console

    @allocated_console.setter
    def allocated_console(self, value: bool):
        self._allocated_console = value

    def __init__(self, attach_to_pid: Optional[int] = None):
        self.attach_to_pid = attach_to_pid
        self.had_console = False
        self.detached_parent = False
        self.allocated_console = False

    def __enter__(self):
        k32 = ctypes.windll.kernel32
        get_console_window = k32.GetConsoleWindow
        get_console_window.restype = ctypes.c_void_p

        self.had_console = bool(get_console_window())
        if self.attach_to_pid is not None and self.had_console:
            k32.FreeConsole()
            self.detached_parent = True

        if self.attach_to_pid is not None:
            attached = bool(k32.AttachConsole(ctypes.c_uint(self.attach_to_pid)))
            if (not attached) and (not self.had_console) and k32.AllocConsole():
                self.allocated_console = True
        else:
            if not self.had_console and k32.AllocConsole():
                self.allocated_console = True
        k32.SetConsoleCtrlHandler(None, True)
        return self

    def __exit__(self, exc_type, exc, tb):
        k32 = ctypes.windll.kernel32
        k32.SetConsoleCtrlHandler(None, False)
        if self.allocated_console or self.attach_to_pid is not None:
            k32.FreeConsole()
        if self.detached_parent:
            ATTACH_PARENT_PROCESS = ctypes.c_uint(0xFFFFFFFF)  # (DWORD)-1
            k32.AttachConsole(ATTACH_PARENT_PROCESS.value)
        return False

    def send_ctrl_break_to_pid(self, pid: int):
        k32 = ctypes.windll.kernel32
        CTRL_BREAK_EVENT = 1
        target = ctypes.c_uint(pid or 0)
        k32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, target)
        time.sleep(0.5)

class WindowsLRRDeploymentContext(AbstractLRRDeploymentContext):
    """
    Set up a LANraragi environment on Windows. Requires a win-dist path and staging directory to be provided.

    Staging directory is out of windist dir, and handles all the files that belong to this deployment.
    This directory is supplied by the user, which should represent an isolated deployment environment.
    The reason we need a staging directory, is that we want to reproduce the docker effect of an isolated
    workspace, where each directory within this parent is a volume which can be cleaned up, giving us a 
    volume inventory when running tests.

    Staging dir will have the following structure. When tearing down and removing everything, will
    remove all directories with the appropriate resource prefix.
    ```
    /path/to/original/win-dist/             # we won't try to touch this.
    /path/to/staging_dir/
        |- {resource_prefix}win-dist/
            |- lib/
            |- locales/
            |- public/
            |- runtime/
            |- script/
            |- templates/
            |- lrr.conf
            |- package.json
            |- run.ps1
        |- {resource_prefix}contents/
            |- thumb/
        |- {resource_prefix}temp/           # move temp out to avoid applying unnecessary Shinobu pressure
        |- {resource_prefix}redis/          # dedicated redis directory (instead of using contents)
        |- {resource_prefix}log/
            |- lanraragi.log
            |- redis.log
        |- {resource_prefix}pid/
            |- redis.pid
            |- server.pid
    ```

    Then, we can open up concurrent testing like this:
    ```
    /path/to/staging_dir/
        |- test_1_resources/
        |- test_2_resources/
        |- ...
    ```
    """

    @property
    def staging_dir(self) -> Path:
        """
        The directory where everything can happen.
        """
        return self._staging_dir

    @staging_dir.setter
    def staging_dir(self, dir: Path):
        self._staging_dir = dir.absolute()

    @property
    def contents_dir(self) -> Path:
        """
        Absolute path to the entire LRR application. For testing purposes,
        contents_dir is determined by windist path + resource prefix.

        At the end of testing, the contents_dir should be removed.
        """
        contents_dirname = self.resource_prefix + "contents"
        return self.staging_dir / contents_dirname
    
    @property
    def redis_dir(self) -> Path:
        """
        Absolute path to the Redis application (according to runfile, is same as contents dir)
        """
        redis_dirname = self.resource_prefix + "redis"
        return self.staging_dir / redis_dirname

    @property
    def thumb_dir(self) -> Path:
        """
        Absolute path to the LRR thumbnail directory
        """
        return self.contents_dir / "thumb"

    @property
    def logs_dir(self) -> Path:
        logs_dir = self.resource_prefix + "log"
        return self.staging_dir / logs_dir
    
    @property
    def pid_dir(self) -> Path:
        pid_dir = self.resource_prefix + "pid"
        return self.staging_dir / pid_dir

    @property
    def temp_dir(self) -> Path:
        temp_dir = self.resource_prefix + "temp"
        return self.staging_dir / temp_dir

    @property
    def lrr_log_path(self) -> Path:
        return self.logs_dir / "lanraragi.log"
    
    @property
    def redis_log_path(self) -> Path:
        return self.logs_dir / "redis.log"
    
    @property
    def redis_server_exe_path(self) -> Path:
        return self.windist_dir / "runtime" / "redis" / "redis-server.exe"

    @property
    def redis_conf(self) -> Path:
        return Path("runtime") / "redis" / "redis.conf"

    @property
    def redis_pid_path(self) -> Path:
        return self.pid_dir / "redis.pid"

    @property
    def server_pid_path(self) -> Path:
        return self.pid_dir / "server.pid"

    @property
    def lrr_address(self) -> str:
        """
        Address of the LRR server (i.e. http://127.0.0.1:$port)
        """
        return f"http://127.0.0.1:{self.lrr_port}"

    @property
    def windist_dir(self) -> Path:
        """
        Absolute path to the LRR distribution directory containing the runfile.
        """
        windist_dir = self.resource_prefix + "win-dist"
        return self.staging_dir / windist_dir

    @property
    def original_windist_dir(self) -> Path:
        return self._original_windist_dir
    
    @original_windist_dir.setter
    def original_windist_dir(self, dir: Path):
        self._original_windist_dir = dir.absolute()

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

    @property
    def lrr_pid(self) -> Optional[int]:
        """
        PID for the LRR process. If not cached, tries to get it via the expected port.
        """
        return _get_port_owner_pid(self.lrr_port)
    
    @property
    def redis_pid(self) -> Optional[int]:
        """
        PID for the Redis process (which is just the owner of the Redis port).
        """
        return _get_port_owner_pid(self.redis_port)

    @property
    def perl_exe_path(self) -> Path:
        """
        Path to perl executable.
        """
        return self.windist_dir / "runtime" / "bin" / "perl.exe"

    @property
    def runtime_bin_dir(self) -> Path:
        return self.windist_dir / "runtime" / "bin"
    
    @property
    def runtime_redis_dir(self) -> Path:
        return self.windist_dir / "runtime" / "redis"
    
    @property
    def lrr_launcherpl_path(self) -> Path:
        return self.windist_dir / "script" / "launcher.pl"
    
    @property
    def lrr_lanraragi_path(self) -> Path:
        return self.windist_dir / "script" / "lanraragi"

    def __init__(
        self, windist_path: str, staging_directory: str, resource_prefix: str, port_offset: int,
        logger: Optional[logging.Logger]=None
    ):
        self.resource_prefix = resource_prefix
        self.port_offset = port_offset

        self.staging_dir = Path(staging_directory)
        self.original_windist_dir = Path(windist_path)

        if logger is None:
            logger = LOGGER
        self.logger = logger

    @override
    def update_api_key(self, api_key: Optional[str]):
        self.lrr_api_key = api_key
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
        Copies original windist dir to the new temporary windist dir (if not already done).

        Teardowns do not necessarily guarantee port availability. Windows may
        keep a port non-bindable for a short period of time even with no visible owning process.

        This setup logic is adapted from the LRR runfile, except we will start redis
        and LRR individually, and inject configuration data between redis/LRR startups
        to avoid having to restart LRR.
        """
        lrr_port = self.lrr_port
        redis_port = self.redis_port
        original_windist_dir = self.original_windist_dir
        if not original_windist_dir.exists():
            raise FileNotFoundError(f"win-dist path {original_windist_dir} not found.")
        
        # create the staging directory.
        staging_dir = self.staging_dir
        if not staging_dir.exists():
            raise FileNotFoundError(f"Staging directory {staging_dir} not found.")

        # copy the windist directory.
        windist_dir = self.windist_dir
        if not windist_dir.exists():
            shutil.copytree(original_windist_dir, windist_dir)
            self.logger.debug(f"Copied original windist directory to {windist_dir}.")
        else:
            self.logger.debug(f"Copy of windist directory exists: {windist_dir}")

        # log the setup resource allocations for user to see
        self.logger.info(f"Deploying Windows LRR with the following resources: LRR port {lrr_port}, Redis port {redis_port}, content path {self.contents_dir}.")

        # create contents, thumb, temp, log, pid, redis.
        contents_dir = self.contents_dir
        thumb_dir = self.thumb_dir
        temp_dir = self.temp_dir
        log_dir = self.logs_dir
        pid_dir = self.pid_dir
        redis_dir = self.redis_dir
        if contents_dir.exists():
            self.logger.debug(f"Contents directory exists: {contents_dir}")
        else:
            self.logger.debug(f"Creating contents dir: {contents_dir}")
            contents_dir.mkdir(parents=True, exist_ok=False)
        if thumb_dir.exists():
            self.logger.debug(f"Thumb directory exists: {thumb_dir}")
        else:
            self.logger.debug(f"Creating thumb directory: {thumb_dir}")
            thumb_dir.mkdir(parents=True, exist_ok=False)
        if temp_dir.exists():
            self.logger.debug(f"Temp directory exists: {temp_dir}")
        else:
            self.logger.debug(f"Creating temp dir: {temp_dir}")
            temp_dir.mkdir(parents=True, exist_ok=False)
        if log_dir.exists():
            self.logger.debug(f"Logs directory exists: {log_dir}")
        else:
            self.logger.debug(f"Creating logs directory: {log_dir}")
            log_dir.mkdir(parents=True, exist_ok=False)
        if pid_dir.exists():
            self.logger.debug(f"PID directory exists: {pid_dir}")
        else:
            self.logger.debug(f"Creating PID directory: {pid_dir}")
            pid_dir.mkdir(parents=True, exist_ok=False)
        if redis_dir.exists():
            self.logger.debug(f"Redis directory exists: {redis_dir}")
        else:
            self.logger.debug(f"Creating Redis directory: {redis_dir}")
            redis_dir.mkdir(parents=True, exist_ok=False)

        # we need to handle cases where existing services are running.
        # Unlike docker, we have no idea whether we can skip recreation of
        # the LRR process, so we will always recreate it.
        if is_port_available(redis_port):
            self.start_redis()
            self._test_redis_connection()
            self.logger.debug(f"Redis service is established on port {redis_port}.")
        else:
            # TODO: this throws an exception if not redis on port or redis broken
            self._test_redis_connection()
            self.logger.debug(f"Running Redis service confirmed on port {redis_port}, skipping startup.")
        if with_api_key:
            self.update_api_key(DEFAULT_API_KEY)
        if with_nofunmode:
            self.enable_nofun_mode()
        if lrr_debug_mode:
            self.enable_lrr_debug_mode()
        self.logger.debug("Redis post-connect configuration complete.")

        if is_port_available(lrr_port):
            self.start_lrr()
            self.test_lrr_connection(lrr_port)
            self.logger.debug(f"LRR service is established on port {lrr_port}.")
        else:
            self.logger.debug(f"Found running LRR service on port {lrr_port}. Restarting...")
            self.stop_lrr()
            self.start_lrr()
            self.logger.debug("LRR service restarted.")

        redis_pid = self.redis_pid
        lrr_pid = self.lrr_pid
        self.logger.info(f"Completed setup of LANraragi. LRR PID = {lrr_pid}; Redis PID = {redis_pid}.")

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
            self.logger.debug(f"Redis service is established on port {redis_port}.")
        else:
            # TODO: this throws an exception if not redis on port or redis broken
            self._test_redis_connection()
            self.logger.debug(f"Running Redis service confirmed on port {redis_port}, skipping startup.")
        self.logger.debug("Started Redis.")

        lrr_port = self.lrr_port
        if is_port_available(lrr_port):
            self.start_lrr()
            self.test_lrr_connection(lrr_port)
            self.logger.debug(f"LRR service established on port {lrr_port}")
        else:
            self.test_lrr_connection(lrr_port)
            self.logger.debug(f"Running LRR service confirmed on port {lrr_port}, skipping startup.")

    @override
    def stop(self):
        self.stop_lrr()
        self.logger.debug("Stopped LRR.")
        self.stop_redis()
        self.logger.debug("Stopped Redis.")

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
        log_dir = self.logs_dir
        pid_dir = self.pid_dir
        windist_dir = self.windist_dir
        redis_dir = self.redis_dir
        temp_dir = self.temp_dir
        self.stop()

        if remove_data:
            if contents_dir.exists():
                self._remove_ro(contents_dir)
                shutil.rmtree(contents_dir)
                self.logger.debug(f"Removed contents directory: {contents_dir}")
            if log_dir.exists():
                self._remove_ro(log_dir)
                shutil.rmtree(log_dir)
                self.logger.debug(f"Removed logs directory: {log_dir}")
            if pid_dir.exists():
                self._remove_ro(pid_dir)
                shutil.rmtree(pid_dir)
                self.logger.debug(f"Removed PID directory: {pid_dir}")
            if windist_dir.exists():
                self._remove_ro(windist_dir)
                shutil.rmtree(windist_dir)
                self.logger.debug(f"Removed windist directory: {windist_dir}")
            if redis_dir.exists():
                self._remove_ro(redis_dir)
                shutil.rmtree(redis_dir)
                self.logger.debug(f"Removed redis directory: {redis_dir}")
            if temp_dir.exists():
                self._remove_ro(temp_dir)
                shutil.rmtree(temp_dir)
                self.logger.debug(f"Removed temp directory: {temp_dir}")

    @override
    def start_lrr(self):
        """
        Executes the LRR portion of tools/build/windows/run.ps1.
        """
        cwd = os.getcwd()

        try:
            windist_path = self.windist_dir
            if not windist_path.exists():
                raise DeploymentException(f"Expected windist {windist_path} to exist.")
            os.chdir(windist_path)

            lrr_network = self.lrr_address
            lrr_data_directory = self.contents_dir
            lrr_log_directory = self.logs_dir
            lrr_temp_directory = self.temp_dir
            lrr_thumb_directory = self.thumb_dir
            if not lrr_log_directory.exists():
                self.logger.debug(f"Making logs directory: {lrr_log_directory}")
                lrr_log_directory.mkdir(parents=True, exist_ok=False)
            else:
                self.logger.debug(f"Logs directory exists: {lrr_log_directory}")
            if not lrr_temp_directory.exists():
                self.logger.debug(f"Making temp directory: {lrr_temp_directory}")
                lrr_temp_directory.mkdir(parents=True, exist_ok=False)
            else:
                self.logger.debug(f"Temp directory exists: {lrr_temp_directory}")
            if not lrr_thumb_directory.exists():
                self.logger.debug(f"Making thumb directory: {lrr_thumb_directory}")
                lrr_thumb_directory.mkdir(parents=True, exist_ok=False)
            else:
                self.logger.debug(f"Thumb directory exists: {lrr_thumb_directory}")

            lrr_env = os.environ.copy()
            path_var = lrr_env.get("Path", lrr_env.get("PATH", ""))
            runtime_bin = str(self.runtime_bin_dir)
            runtime_redis = str(self.runtime_redis_dir)
            lrr_env["LRR_NETWORK"] = lrr_network
            lrr_env["LRR_DATA_DIRECTORY"] = str(lrr_data_directory)
            lrr_env["LRR_LOG_DIRECTORY"] = str(lrr_log_directory)
            lrr_env["LRR_TEMP_DIRECTORY"] = str(lrr_temp_directory)
            lrr_env["LRR_THUMB_DIRECTORY"] = str(lrr_thumb_directory)
            lrr_env["LRR_REDIS_ADDRESS"] = f"127.0.0.1:{self.redis_port}"
            lrr_env["Path"] = runtime_bin + os.pathsep + runtime_redis + os.pathsep + path_var if path_var else runtime_bin + os.pathsep + runtime_redis

            script = [
                str(self.perl_exe_path), str(self.lrr_launcherpl_path),
                "-d", str(self.lrr_lanraragi_path)
            ]
            self.logger.info(f"(lrr_network={lrr_network}, lrr_data_directory={lrr_data_directory}, lrr_log_directory={lrr_log_directory}, lrr_temp_directory={lrr_temp_directory}, lrr_thumb_directory={lrr_thumb_directory}) running script {subprocess.list2cmdline(script)}")

            # Ensure we have a console so the child inherits it (or gets its own), and create a new
            # process group so we can signal with CTRL_BREAK later. Also create a new console so LRR
            # does not share the test runner console.
            CREATE_NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            CREATE_NEW_CONSOLE: int = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            with _WindowsConsole():
                lrr_process = subprocess.Popen(
                    script,
                    env=lrr_env,
                    creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE,
                )
            self.logger.debug(f"Started LRR process with PID: {lrr_process.pid}.")
        finally:
            os.chdir(cwd)

    @override
    def start_redis(self):
        """
        Executes the Redis portion of tools/build/windows/run.ps1.
        """
        cwd = os.getcwd()

        try:
            windist_path = self.windist_dir.absolute()
            if not windist_path.exists():
                raise DeploymentException(f"Expected windist {windist_path} to exist.")
            os.chdir(windist_path)

            logs_dir = self.logs_dir
            redis_server_path = self.redis_server_exe_path
            pid_filepath = self.redis_pid_path
            redis_dir = self.redis_dir
            redis_logfile_path = self.redis_log_path

            if not logs_dir.exists():
                self.logger.debug(f"Creating logs directory: {logs_dir}")
                logs_dir.mkdir(parents=True, exist_ok=False)
            if not redis_dir.exists():
                self.logger.debug(f"Creating redis directory: {redis_dir}")
                redis_dir.mkdir(parents=True, exist_ok=False)

            script = [
                str(redis_server_path), str(self.redis_conf),
                "--pidfile", str(pid_filepath), # maybe we don't need this...?
                "--dir", str(redis_dir),
                "--logfile", str(redis_logfile_path),
                "--port", str(self.redis_port),
            ]
            self.logger.debug(f"(redis_dir={redis_dir}, redis_logfile_path={redis_logfile_path}) running script {subprocess.list2cmdline(script)}")
            redis_process = subprocess.Popen(script)
            self.logger.debug(f"Started redis service with PID {redis_process.pid}.")
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
        port = self.lrr_port
        deadline = time.time() + timeout
        if is_port_available(port):
            self.logger.debug(f"Confirmed port availability on port: {port}")
            return
        elif pid := self.lrr_pid:
            with _WindowsConsole(attach_to_pid=pid) as windows_console:
                windows_console.send_ctrl_break_to_pid(pid)
            self.logger.info(f"Shutting down LRR (pid={pid}) with CTRL_BREAK_EVENT; waiting...")
            while time.time() < deadline:
                if is_port_available(port):
                    self.logger.debug(f"Confirmed LRR port availability: {port}")
                    return
                time.sleep(1)
            self.logger.warning('LRR port still occupied after graceful shutdown.')
        else:
            # case: port is not available, but no PID found: proceed to kill by perl process.
            self.logger.warning(f"No owners found for occupied port: {port}")
        del deadline

        # We have the following cases to handle (port not available) after graceful shutdown:
        # 1. Port is still occupied by PID.
        # 2. Port is occupied but we don't know owner.
        self.logger.debug("Attempting to kill LRR process...")
        is_free_times = 0 # add a counter to track revived port claimers.
        free_times_threshold = 4
        tts = 0.5
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pid := self.lrr_pid:
                if is_free_times:
                    self.logger.debug(f"Killing LRR process (is_free_times = {is_free_times} has been reset): {pid}")
                else:
                    self.logger.debug(f"Killing LRR process: {pid}")
                is_free_times = 0 # reset this counter if we have a newfound port claimer.
                script = [
                    "taskkill", "/PID", str(pid), "/F", "/T"
                ]
                output = subprocess.run(script, capture_output=True, text=True)
                if output.returncode != 0:
                    raise DeploymentException(f"Failed to stop LRR process with script {subprocess.list2cmdline(script)} ({output.returncode}): STDERR={output.stderr}")
                else:
                    self.logger.debug(f"Killed LRR process {pid}. Output: {output.stdout}")
                    time.sleep(0.2)
            else:
                if is_free_times >= free_times_threshold:
                    # second-to-last sanity check.
                    if is_port_available(port):
                        # this will guarantee that we have LRR port available for 1.5s.
                        self.logger.debug(f"Confirmed LRR port availability on {port}")
                        return
                    else:
                        # nope, perl kill is needed.
                        # TODO: if we don't see these warning logs for a while, we should just remove them.
                        self.logger.warning(f"No owners of port {port} found, but port is not available.")
                        self._kill_lrr_perl_processes_by_path()
                        self.logger.debug("Perl process purge complete.")

                        # one final check.
                        if is_port_available(port):
                            self.logger.debug(f"Confirmed LRR port availability on {port} after purging Perl processes.")
                            return
                        else:
                            raise DeploymentException(f"Failed to provide port {port} availability after killing Perl processes!")
                is_free_times += 1
                if is_port_available(port):
                    self.logger.debug(f"Port available: {port} ({is_free_times}/{free_times_threshold})")
                else:
                    self.logger.warning(f"No owners found for occupied port: {port} ({is_free_times}/{free_times_threshold})")
                time.sleep(tts)

        raise DeploymentException(f"Failed to kill LRR process and provide port availability within {timeout}s!")

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

    # TODO: I hope we don't have to use this.
    def _kill_lrr_perl_processes_by_path(self):
        """
        Kill perl.exe processes started from within the win-dist runtime path.
        """
        perl_path = str(self.perl_exe_path)
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
        if not pids:
            self.logger.warning("No perl.exe processes found in win-dist runtime path to kill.")
            return
        for p in pids:
            output = subprocess.run(["taskkill", "/PID", p, "/F", "/T"], capture_output=True, text=True)
            if output.returncode == 0:
                self.logger.debug(f"Killed perl process {p}: STDOUT = {output.stdout}")
            elif output.returncode == 128:
                # Already terminated by a previous tree kill; not an error
                self.logger.debug(f"Perl process {p} not found (already terminated).")
            else:
                raise DeploymentException(f"Failed to stop perl LRR process ({output.returncode}): STDERR={output.stderr}")

    def _remove_ro(self, dir: Path):
        """
        Recursively clear Windows Read-only attributes so directories can be removed.
        """
        dir.chmod(dir.stat().st_mode | stat.S_IWRITE)
        for root, dirs, files in os.walk(dir, topdown=False):
            root_path = Path(root)
            for name in files:
                p = root_path / name
                p.chmod(p.stat().st_mode | stat.S_IWRITE)
            for name in dirs:
                p = root_path / name
                p.chmod(p.stat().st_mode | stat.S_IWRITE)

def _get_port_owner_pid(port: int) -> Optional[int]:
    # Prefer LISTEN state owner to avoid TIME_WAIT rows (OwningProcess 0)
    ps = (
        f"$p={port}; "
        "Get-NetTCPConnection -LocalPort $p | "
        "Where-Object { ($_.State -eq 'Listen' -or $_.State -eq 2) -and ($_.LocalAddress -eq '127.0.0.1' -or $_.LocalAddress -eq '0.0.0.0') } | "
        "Select-Object -First 1 -ExpandProperty OwningProcess"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps],
        capture_output=True, text=True
    )
    pid = result.stdout.strip()
    return int(pid) if pid.isdigit() else None
