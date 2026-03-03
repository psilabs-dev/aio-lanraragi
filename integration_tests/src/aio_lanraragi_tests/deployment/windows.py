"""
Windows LRR deployment module.
"""

import contextlib
import ctypes
import logging
import os
import shutil
import stat
import subprocess
import threading
import time
from collections import deque
from contextlib import AbstractContextManager
from pathlib import Path
from typing import override

import psutil
import redis

from aio_lanraragi_tests.common import DEFAULT_API_KEY, is_port_available
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    PluginPathsT,
)
from aio_lanraragi_tests.exceptions import DeploymentException

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
    def attach_to_pid(self) -> int | None:
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

    def __init__(self, attach_to_pid: int | None = None):
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
    """

    @override
    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @override
    @property
    def archives_dir(self) -> Path:
        contents_dirname = self.resource_prefix + "archives"
        return self.staging_dir / contents_dirname

    @override
    @property
    def thumb_dir(self) -> Path:
        """
        Absolute path to the LRR thumbnail directory
        """
        thumb_dirname = self.resource_prefix + "thumb"
        return self.staging_dir / thumb_dirname

    @override
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
    def original_windist_dir(self, directory: Path):
        self._original_windist_dir = directory.absolute()

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
    def lrr_pid(self) -> int | None:
        """
        PID for the LRR process. If not cached, tries to get it via the expected port.
        """
        return _get_port_owner_pid(self.lrr_port)

    @property
    def redis_pid(self) -> int | None:
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

    @property
    def lrr_plugin_dir(self) -> Path:
        return self.windist_dir / "lib" / "LANraragi" / "Plugin"

    def __init__(
        self, windist_path: str, staging_directory: str, resource_prefix: str, port_offset: int,
        logger: logging.Logger | None=None
    ):
        self.resource_prefix = resource_prefix
        self.port_offset = port_offset

        self._staging_dir = Path(staging_directory)
        self.original_windist_dir = Path(windist_path)

        if logger is None:
            logger = LOGGER
        self.logger = logger
        self._lrr_process = None
        self._lrr_output = deque(maxlen=10000)
        self._lrr_reader_thread = None

    @override
    def setup(
        self, with_api_key: bool=False, with_nofunmode: bool=False, enable_cors: bool=False, lrr_debug_mode: bool=False,
        environment: dict[str, str]={}, plugin_paths: PluginPathsT={},
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

        Args:
            with_api_key: whether to add a default API key to LRR
            with_nofunmode: whether to start LRR with nofunmode on
            enable_cors: whether to enable/disable CORS during startup
            lrr_debug_mode: whether to start LRR with debug mode on
            environment: additional environment variables map to pass through to the LRR process
            test_connection_max_retries: connection retries for server readiness
        """
        # Store environment overrides for use during process launch
        self._setup_environment = dict(environment or {})
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
            t0 = time.time()
            shutil.copytree(original_windist_dir, windist_dir)
            self._copytree_elapsed = time.time() - t0
            self.logger.info(f"Copied windist directory ({self._copytree_elapsed:.2f}s): {windist_dir}")
        else:
            self._copytree_elapsed = 0.0
            self.logger.info(f"Reusing existing windist directory: {windist_dir}")
        self.plugin_paths = plugin_paths
        self.apply_plugins()

        # log the setup resource allocations for user to see
        self.logger.info(f"Deploying Windows LRR with the following resources: LRR port {lrr_port}, Redis port {redis_port}, content path {self.archives_dir}.")

        # create contents, thumb, temp, log, pid, redis.
        contents_dir = self.archives_dir
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
            self.test_redis_connection()
            self.logger.debug(f"Redis service is established on port {redis_port}.")
        else:
            # TODO: this throws an exception if not redis on port or redis broken
            self.test_redis_connection()
            self.logger.debug(f"Running Redis service confirmed on port {redis_port}, skipping startup.")
        if with_api_key:
            self.update_api_key(DEFAULT_API_KEY)
        if with_nofunmode:
            self.enable_nofun_mode()
        if lrr_debug_mode:
            self.enable_lrr_debug_mode()
        if enable_cors:
            self.enable_cors()
        else:
            self.disable_cors()
        self.logger.debug("Redis post-connect configuration complete.")

        disk_before = psutil.disk_io_counters()
        mem_before = psutil.virtual_memory()
        t0 = time.time()
        if is_port_available(lrr_port):
            self.start_lrr()
            self.test_lrr_connection(lrr_port)
            self.logger.debug(f"LRR service is established on port {lrr_port}.")
        else:
            self.logger.debug(f"Found running LRR service on port {lrr_port}. Restarting...")
            self.stop_lrr()
            self.start_lrr()
            self.logger.debug("LRR service restarted.")
        disk_after = psutil.disk_io_counters()
        timing_extra = {
            "copytree_seconds": round(self._copytree_elapsed, 3),
            "available_mem_mb": round(mem_before.available / (1024 * 1024), 1),
        }
        if disk_before and disk_after:
            timing_extra["disk_read_bytes"] = disk_after.read_bytes - disk_before.read_bytes
            timing_extra["disk_read_ops"] = disk_after.read_count - disk_before.read_count
            timing_extra["disk_write_bytes"] = disk_after.write_bytes - disk_before.write_bytes
            timing_extra["disk_write_ops"] = disk_after.write_count - disk_before.write_count
        self._record_timing("setup", time.time() - t0, **timing_extra)

        redis_pid = self.redis_pid
        lrr_pid = self.lrr_pid
        self.logger.info(f"Completed setup of LANraragi. LRR PID = {lrr_pid}; Redis PID = {redis_pid}.")

    @override
    def apply_plugins(self):
        root_dir = self.lrr_plugin_dir
        for plugin_type, plugin_paths in self.plugin_paths.items():
            if not plugin_paths:
                continue

            target_testing_dir = root_dir / plugin_type / "Testing"
            if target_testing_dir.exists():
                shutil.rmtree(target_testing_dir)
            target_testing_dir.mkdir(parents=True, exist_ok=False)

            for plugin_path in plugin_paths:
                source = Path(plugin_path)
                if not source.exists():
                    raise FileNotFoundError(f"Plugin path does not exist: {source}")
                shutil.copy2(source, target_testing_dir / source.name)

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
            self.test_redis_connection()
            self.logger.debug(f"Redis service is established on port {redis_port}.")
        else:
            # TODO: this throws an exception if not redis on port or redis broken
            self.test_redis_connection()
            self.logger.debug(f"Running Redis service confirmed on port {redis_port}, skipping startup.")
        self.logger.debug("Started Redis.")

        lrr_port = self.lrr_port
        disk_before = psutil.disk_io_counters()
        mem_before = psutil.virtual_memory()
        t0 = time.time()
        if is_port_available(lrr_port):
            self.start_lrr()
            self.test_lrr_connection(lrr_port)
            self.logger.debug(f"LRR service established on port {lrr_port}")
        else:
            self.test_lrr_connection(lrr_port)
            self.logger.debug(f"Running LRR service confirmed on port {lrr_port}, skipping startup.")
        disk_after = psutil.disk_io_counters()
        timing_extra = {
            "available_mem_mb": round(mem_before.available / (1024 * 1024), 1),
        }
        if disk_before and disk_after:
            timing_extra["disk_read_bytes"] = disk_after.read_bytes - disk_before.read_bytes
            timing_extra["disk_read_ops"] = disk_after.read_count - disk_before.read_count
            timing_extra["disk_write_bytes"] = disk_after.write_bytes - disk_before.write_bytes
            timing_extra["disk_write_ops"] = disk_after.write_count - disk_before.write_count
        self._record_timing("start", time.time() - t0, **timing_extra)

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

    def _start_lrr_output_reader(self, pipe):
        def _reader():
            for line in iter(pipe.readline, b''):
                self._lrr_output.append(line.replace(b'\r\n', b'\n'))
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        self._lrr_reader_thread = t

    @override
    def teardown(self, remove_data: bool=False):
        """
        Forceful shutdown of LRR and Redis and remove the content path, preparing it for another test.
        Additionally, close all closable resources/clients.
        """
        contents_dir = self.archives_dir
        log_dir = self.logs_dir
        pid_dir = self.pid_dir
        windist_dir = self.windist_dir
        redis_dir = self.redis_dir
        temp_dir = self.temp_dir
        self.stop()
        if hasattr(self, "_redis_client") and self._redis_client is not None:
            self._redis_client.close()
        if remove_data:
            t0 = time.time()
            for label, d in [
                ("contents", contents_dir), ("logs", log_dir), ("pid", pid_dir),
                ("windist", windist_dir), ("redis", redis_dir), ("temp", temp_dir),
            ]:
                if d.exists():
                    self._remove_ro(d)
                    shutil.rmtree(d)
            elapsed = time.time() - t0
            self.logger.info(f"Teardown rmtree completed in {elapsed:.2f}s.")

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
            lrr_data_directory = self.archives_dir
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
            # Apply setup-provided environment variables, overriding defaults where specified
            if hasattr(self, "_setup_environment") and self._setup_environment:
                lrr_env.update(self._setup_environment)

            script = [
                str(self.perl_exe_path), str(self.lrr_launcherpl_path),
                "-d", str(self.lrr_lanraragi_path)
            ]
            self.logger.info(f"(lrr_network={lrr_network}, lrr_data_directory={lrr_data_directory}, lrr_log_directory={lrr_log_directory}, lrr_temp_directory={lrr_temp_directory}, lrr_thumb_directory={lrr_thumb_directory}) running script {subprocess.list2cmdline(script)}")

            # Ensure we have a console so the child inherits it (or gets its own), and create a new
            # process group so we can signal with CTRL_BREAK later.
            CREATE_NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            with _WindowsConsole():
                lrr_process = subprocess.Popen(
                    script,
                    env=lrr_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=CREATE_NEW_PROCESS_GROUP,
                )
            self._lrr_process = lrr_process
            if lrr_process.stdout:
                self._start_lrr_output_reader(lrr_process.stdout)
            self.logger.debug(f"Started LRR process with PID: {lrr_process.pid}.")
        finally:
            os.chdir(cwd)

    @override
    def _log_redis_connect_diagnostics(self, retry_count: int, max_retries: int):
        proc = getattr(self, "_redis_process", None)
        port = self.redis_port
        port_avail = is_port_available(port)
        if proc is not None:
            rc = proc.poll()
            self.logger.warning(
                f"Redis connect diagnostic ({retry_count+1}/{max_retries}): "
                f"process.pid={proc.pid}, process.poll()={rc}, port_available={port_avail}"
            )
        else:
            self.logger.warning(
                f"Redis connect diagnostic ({retry_count+1}/{max_retries}): "
                f"no process handle, port_available={port_avail}"
            )
        # Log captured Redis stderr/stdout if available.
        for label, path_attr in [("stderr", "_redis_stderr_path"), ("stdout", "_redis_stdout_path")]:
            fpath = getattr(self, path_attr, None)
            if fpath and Path(fpath).exists():
                content = Path(fpath).read_text(errors="replace").strip()
                if content:
                    self.logger.warning(f"Redis {label}: {content}")
        # Log TCP state for the port.
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            port_lines = [line.strip() for line in result.stdout.splitlines() if f":{port}" in line]
            if port_lines:
                self.logger.warning(f"TCP state for port {port}: {port_lines}")
        except (OSError, subprocess.TimeoutExpired) as e:
            self.logger.warning(f"Failed to query netstat: {e}")

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

            # Check for orphaned redis-server.exe processes from previous cycles.
            try:
                scan = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq redis-server.exe", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if "redis-server.exe" in scan.stdout:
                    self.logger.warning(f"Pre-start: existing redis-server.exe detected: {scan.stdout.strip()}")
            except (OSError, subprocess.TimeoutExpired) as e:
                self.logger.warning(f"Pre-start: tasklist scan failed: {e}")

            # Log TCP state for the Redis port before starting.
            try:
                ns = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                port_lines = [line.strip() for line in ns.stdout.splitlines() if f":{self.redis_port}" in line]
                if port_lines:
                    self.logger.warning(f"Pre-start TCP state for port {self.redis_port}: {port_lines}")
            except (OSError, subprocess.TimeoutExpired) as e:
                self.logger.warning(f"Pre-start: netstat failed: {e}")

            script = [
                str(redis_server_path), str(self.redis_conf),
                "--pidfile", str(pid_filepath), # maybe we don't need this...?
                "--dir", str(redis_dir),
                "--logfile", str(redis_logfile_path),
                "--port", str(self.redis_port),
            ]
            self.logger.debug(f"(redis_dir={redis_dir}, redis_logfile_path={redis_logfile_path}) running script {subprocess.list2cmdline(script)}")

            # Capture stdout/stderr to files (Redis may print errors before opening its logfile).
            # File handles are intentionally kept open for the subprocess lifetime
            # and closed in stop_redis().
            self._redis_stdout_path = logs_dir / "redis-stdout.log"
            self._redis_stderr_path = logs_dir / "redis-stderr.log"
            self._redis_stdout_fh = open(self._redis_stdout_path, "w")  # noqa: SIM115
            self._redis_stderr_fh = open(self._redis_stderr_path, "w")  # noqa: SIM115
            self._redis_process = subprocess.Popen(
                script,
                stdout=self._redis_stdout_fh,
                stderr=self._redis_stderr_fh,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.logger.debug(f"Started redis service with PID {self._redis_process.pid}.")
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
                output = subprocess.run(
                    script,
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
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
        """
        Stop the Redis server via direct process kill.

        Uses TerminateProcess (proc.terminate) instead of cooperative SHUTDOWN
        because Redis for Windows takes ~13s to release its listening socket
        after SHUTDOWN. With CREATE_NO_WINDOW on the Popen call, there is no
        conhost.exe to orphan, so hard-kill is safe. TerminateProcess causes
        the kernel to RST all sockets (no TIME_WAIT), so the port is freed
        immediately.

        Falls back to port-based PID lookup + taskkill if no stored handle.
        """
        port = self.redis_port
        proc = getattr(self, "_redis_process", None)

        # Close stdout/stderr file handles so teardown rmtree can delete the files.
        for attr in ("_redis_stdout_fh", "_redis_stderr_fh"):
            fh = getattr(self, attr, None)
            if fh is not None:
                with contextlib.suppress(OSError):
                    fh.close()
                setattr(self, attr, None)

        # Kill via stored process handle.
        if proc is not None:
            rc = proc.poll()
            if rc is None:
                self.logger.info(f"Killing Redis process (pid={proc.pid}) via terminate.")
                self._kill_redis_process(proc)
            else:
                self.logger.info(f"Redis process (pid={proc.pid}) already exited with rc={rc}.")
            self._redis_process = None
        else:
            self.logger.info("No stored Redis process handle.")

        # Verify port is free; if not, fall back to port-owner kill.
        if is_port_available(port):
            self.logger.info(f"Redis port {port} is available.")
            return

        # Port still occupied — something else holds it, or process hasn't fully exited.
        pid = self.redis_pid
        if pid:
            self.logger.warning(f"Redis port {port} still occupied by pid={pid}; killing via taskkill.")
            self._taskkill_pid(pid)
        else:
            self.logger.warning(f"Redis port {port} occupied but no owning PID found.")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_port_available(port):
                self.logger.info(f"Redis port {port} available after fallback kill.")
                return
            time.sleep(0.5)

        raise DeploymentException(f"Failed to stop Redis and free port {port}!")

    def _kill_redis_process(self, proc: subprocess.Popen):
        """Kill a Redis process via its Popen handle, then fall back to taskkill."""
        pid = proc.pid
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
                self.logger.info(f"Redis process {pid} terminated via handle (rc={proc.returncode}).")
                return
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Redis process {pid} did not exit after terminate(); escalating to taskkill.")
        except OSError as e:
            self.logger.warning(f"Failed to terminate Redis process {pid} via handle: {e}")
        self._taskkill_pid(pid)

    def _taskkill_pid(self, pid: int):
        """Forcefully kill a process by PID using taskkill /F /T."""
        output = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if output.returncode == 0:
            self.logger.info(f"taskkill killed process {pid}. Output: {output.stdout.strip()}")
        elif output.returncode == 128:
            self.logger.info(f"taskkill: process {pid} already terminated.")
        else:
            self.logger.warning(f"taskkill failed for process {pid} (rc={output.returncode}): {output.stderr.strip()}")

    @override
    def get_lrr_logs(self, tail: int=100) -> bytes:
        if self.lrr_log_path.exists():
            with open(self.lrr_log_path, 'rb') as rb:
                lines = rb.readlines()
                if lines:
                    normalized_lines = [line.replace(b'\r\n', b'\n') for line in lines]
                    return b''.join(normalized_lines[-tail:])
                self.logger.error(f"No lines found in {self.lrr_log_path}")
        if hasattr(self, "_lrr_output") and self._lrr_output:
            self.logger.error("LRR logs not found; falling back to console.")
            lines = list(self._lrr_output)
            return b''.join(lines[-tail:])

        self.logger.error("No LRR logs are available!")
        return b"No LRR logs available."

    @override
    def get_redis_logs(self, tail: int=100) -> bytes:
        if self.redis_log_path.exists():
            with open(self.redis_log_path, 'rb') as rb:
                lines = rb.readlines()
                if lines:
                    normalized_lines = [line.replace(b'\r\n', b'\n') for line in lines]
                    return b''.join(normalized_lines[-tail:])
                self.logger.error(f"No lines found in {self.redis_log_path}")
        self.logger.error("No Redis logs are available!")
        return b"No Redis logs available."

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
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip().isdigit()]
        if not pids:
            self.logger.warning("No perl.exe processes found in win-dist runtime path to kill.")
            return
        for p in pids:
            output = subprocess.run(
                ["taskkill", "/PID", p, "/F", "/T"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if output.returncode == 0:
                self.logger.debug(f"Killed perl process {p}: STDOUT = {output.stdout}")
            elif output.returncode == 128:
                # Already terminated by a previous tree kill; not an error
                self.logger.debug(f"Perl process {p} not found (already terminated).")
            else:
                raise DeploymentException(f"Failed to stop perl LRR process ({output.returncode}): STDERR={output.stderr}")

    def _remove_ro(self, directory: Path):
        """
        Recursively clear Windows Read-only attributes so directories can be removed.
        """
        directory.chmod(directory.stat().st_mode | stat.S_IWRITE)
        for root, dirs, files in os.walk(directory, topdown=False):
            root_path = Path(root)
            for name in files:
                p = root_path / name
                p.chmod(p.stat().st_mode | stat.S_IWRITE)
            for name in dirs:
                p = root_path / name
                p.chmod(p.stat().st_mode | stat.S_IWRITE)

def _get_port_owner_pid(port: int) -> int | None:
    # Prefer LISTEN state owner to avoid TIME_WAIT rows (OwningProcess 0)
    ps = (
        f"$p={port}; "
        "Get-NetTCPConnection -LocalPort $p | "
        "Where-Object { ($_.State -eq 'Listen' -or $_.State -eq 2) -and ($_.LocalAddress -eq '127.0.0.1' -or $_.LocalAddress -eq '0.0.0.0') } | "
        "Select-Object -First 1 -ExpandProperty OwningProcess"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError:
        # Console/handle issues can occur in CI; treat as unknown PID.
        return None
    pid = result.stdout.strip()
    return int(pid) if pid.isdigit() else None
