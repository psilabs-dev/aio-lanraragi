import io
import logging
from pathlib import Path
import redis
import shutil
import subprocess
import threading
import time
from typing import Optional, override
import requests

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import is_port_available
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.common import DEFAULT_API_KEY

LOGGER = logging.getLogger(__name__)
KILL_TIMEOUT = 10


class WindowsLRRDeploymentContext(AbstractLRRDeploymentContext):
    """
    Set up a LANraragi environment on Windows. Requires a runfile to be provided.
    """

    def __init__(
        self, runfile: str, testing_workspace: str,
        logger: Optional[logging.Logger]=None,
        init_with_api_key: bool=False, init_with_nofunmode: bool=False, init_with_allow_uploads: bool=False,
        lrr_port: int=3001
    ):
        self.runfile = Path(runfile)
        if logger is None:
            logger = LOGGER
        self.logger = logger
        self.lrr_port = lrr_port
        self.redis_port = 6379 # maybe this can be changed?

        self.init_with_api_key = init_with_api_key
        self.init_with_nofunmode = init_with_nofunmode
        self.init_with_allow_uploads = init_with_allow_uploads

        self.network = f"http://127.0.0.1:{lrr_port}"
        self.content_path = Path(testing_workspace).absolute()
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
    def add_api_key(self, api_key: str):
        self.redis.select(2)
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
    def setup(self, test_connection_max_retries: int = 4):
        runfile = self.runfile.absolute()
        if not runfile.exists():
            raise FileNotFoundError(f"Runfile {runfile} not found.")

        self.get_logger().info("Checking if ports are available.")
        if not is_port_available(self.lrr_port):
            raise DeploymentException(f"Port {self.lrr_port} is occupied.")
        if not is_port_available(self.redis_port):
            raise DeploymentException(f"Redis port {self.redis_port} is occupied.")
        self.get_logger().info("Creating required directories...")
        self.content_path.mkdir(parents=True, exist_ok=True)
        self.thumb_path.mkdir(parents=True, exist_ok=True)

        self._execute_lrr_runfile()
        
        # post LRR startup
        self.get_logger().info("Setup script execution complete; testing connection to LRR server.")
        self._test_lrr_connection(test_connection_max_retries)

        # connect to redis
        self._test_redis_connection()
        restart_required = False
        if self.init_with_api_key:
            self.get_logger().info("Adding API key to Redis...")
            self.add_api_key(DEFAULT_API_KEY)
            restart_required = True
        if self.init_with_nofunmode:
            self.get_logger().info("Enabling NoFun mode...")
            self.enable_nofun_mode()
            restart_required = True
        if self.init_with_allow_uploads:
            self.get_logger().info("LRR services on Windows allow uploads by default. No action needed")

        if restart_required:
            self.get_logger().info("Restart require detected; restarting LRR and Redis...")
            self.stop_lrr()
            self.stop_redis()
            self._execute_lrr_runfile()
            self._test_lrr_connection(test_connection_max_retries)
            self._test_redis_connection()
            self.get_logger().info("Restart complete.")

        self.get_logger().debug("Collecting PID info...")
        self.redis_pid = self._get_redis_pid()
        self.lrr_pid = self._get_lrr_pid()

        self.get_logger().info(f"Completed setup of LANraragi. LRR PID = {self.lrr_pid}; Redis PID = {self.redis_pid}.")

    @override
    def teardown(self):
        """
        Forceful shutdown of LRR and Redis and remove the content path, preparing it for another test.
        """
        if self.lrr_pid and self._is_running(self.lrr_pid):
            script = [
                "taskkill", "/PID", str(self.lrr_pid), "/F",
            ]
            self.get_logger().info("Shutting down LRR with script: " + subprocess.list2cmdline(script))
            subprocess.run(script)
            self.get_logger().info("LRR shutdown complete.")
        self.lrr_pid = None
        if self.redis_pid and self._is_running(self.redis_pid):
            script = [
                "taskkill", "/PID", str(self.redis_pid), "/F",
            ]
            self.get_logger().info("Shutting down Redis with script: " + subprocess.list2cmdline(script))
            subprocess.run(script)
            self.get_logger().info("Redis shutdown complete.")
        self.redis_pid = None

        if self.content_path.exists():
            self.get_logger().info(f"Removing content path: {self.content_path}")
            shutil.rmtree(self.content_path)

    @override
    def start_lrr(self):
        raise NotImplementedError

    @override
    def start_redis(self):
        raise NotImplementedError

    @override
    def stop_lrr(self, timeout: int = 10):
        pid = self._get_lrr_pid()
        if not pid:
            self.get_logger().warning("No LRR PID found, skipping shutdown.")
            return

        output = subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"])
        if output.returncode != 0:
            self.get_logger().error(f"LRR PID {pid} shutdown failed with exit code {output.returncode}")
        else:
            self.get_logger().info(f"LRR PID {pid} shutdown output: {output.stdout}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get_lrr_pid() is None and is_port_available(self.lrr_port):
                self.get_logger().info(f"LRR PID {pid} shutdown complete.")
                return

            time.sleep(0.2)
        raise DeploymentException(f"LRR PID {pid} is still running after {timeout}s, forcing shutdown.")

    @override
    def stop_redis(self, timeout: int = 10):
        pid = self._get_redis_pid()
        if not pid:
            self.get_logger().warning("No Redis PID found, skipping shutdown.")
            return

        output = subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"])
        if output.returncode != 0:
            self.get_logger().error(f"Redis PID {pid} shutdown failed with exit code {output.returncode}")
        else:
            self.get_logger().info(f"Redis PID {pid} shutdown output: {output.stdout}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._get_redis_pid() is None and is_port_available(self.redis_port):
                self.get_logger().info(f"Redis PID {pid} shutdown complete.")
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
        cmd = f"Get-NetTCPConnection -LocalPort {self.lrr_port} | Select-Object -First 1 -ExpandProperty OwningProcess"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True
        )
        pid = result.stdout.strip()
        return int(pid) if pid.isdigit() else None

    def _get_redis_pid(self) -> Optional[int]:
        # see _get_lrr_pid for explanation
        cmd = f"Get-NetTCPConnection -LocalPort {self.redis_port} | Select-Object -First 1 -ExpandProperty OwningProcess"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True
        )
        pid = result.stdout.strip()
        return int(pid) if pid.isdigit() else None

    def _test_lrr_connection(self, test_connection_max_retries: int=4):
        retry_count = 0
        while True:
            try:
                resp = requests.get(f"http://127.0.0.1:{self.lrr_port}")
                if resp.status_code != 200:
                    self.teardown()
                    raise DeploymentException(f"Response status code is not 200: {resp.status_code}")
                else:
                    break
            except requests.exceptions.ConnectionError:
                if retry_count < test_connection_max_retries:
                    time_to_sleep = 2 ** (retry_count + 1)
                    self.get_logger().warning(f"Could not reach LRR server ({retry_count+1}/{test_connection_max_retries}); retrying after {time_to_sleep}s.")
                    retry_count += 1
                    time.sleep(time_to_sleep)
                    continue
                else:
                    self.get_logger().error("Failed to connect to LRR server! Dumping logs and shutting down server.")
                    self.display_lrr_logs()
                    self.teardown()
                    raise DeploymentException("Failed to connect to the LRR server!")

    def _test_redis_connection(self):
        self.get_logger().info("Connecting to Redis...")
        if not self.redis:
            self.redis = redis.Redis(host="127.0.0.1", port=self.redis_port, decode_responses=True)
        self.redis.ping()
        self.get_logger().info("Redis connection established.")
