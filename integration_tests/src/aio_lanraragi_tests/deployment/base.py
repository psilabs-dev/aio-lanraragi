import abc
import gzip
import logging
import shutil
import time
from pathlib import Path
from typing import Literal

import aiohttp
import redis
import requests
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.common import DEFAULT_LRR_PORT, DEFAULT_REDIS_PORT
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.log_parse import parse_lrr_logs

PluginPathsT = dict[Literal["Download", "Login", "Metadata", "Scripts"], list[str]]


class AbstractLRRDeploymentContext(abc.ABC):

    @property
    def logger(self) -> logging.Logger:
        """
        Logger implementation assigned to a deployment.
        """
        return self._logger

    @logger.setter
    def logger(self, logger: logging.Logger):
        self._logger = logger

    @property
    def resource_prefix(self) -> str:
        """
        String to be attached to the beginning of most provisioned deployment resources.
        This string SHOULD be "test_" or "test_integration_", or "test_{case_no}_" in a
        distributed testing workflow.
        """
        return self._resource_prefix

    @resource_prefix.setter
    def resource_prefix(self, new_resource_prefix: str):
        self._resource_prefix = new_resource_prefix

    @property
    def port_offset(self) -> int:
        """
        A number to be added to all default service ports (LRR and Redis) during testing.
        This number SHOULD be between 10-20 (inclusive).
        """
        return self._port_offset

    @port_offset.setter
    def port_offset(self, new_port_offset: int):
        self._port_offset = new_port_offset

    @property
    def lrr_port(self) -> int:
        """
        Port exposed for the given LRR application.
        """
        return DEFAULT_LRR_PORT + self.port_offset

    @property
    def redis_port(self) -> int:
        """
        Port exposed for the given Redis database.
        """
        return DEFAULT_REDIS_PORT + self.port_offset

    @property
    def lrr_base_url(self) -> str:
        """
        Base URL for this instance. Defaults to `"http://127.0.0.1:$lrr_port"`.

        Currently a readonly property.
        """
        if not hasattr(self, "_lrr_base_url"):
            self._lrr_base_url = f"http://127.0.0.1:{self.lrr_port}"
        return self._lrr_base_url

    @property
    def lrr_api_key(self) -> str | None:
        """
        Unencoded API key for this instance.
        Can be None, which represents a server without an API key.
        """
        if not hasattr(self, "_lrr_api_key"):
            return None
        return self._lrr_api_key

    @lrr_api_key.setter
    def lrr_api_key(self, lrr_api_key: str | None):
        self._lrr_api_key = lrr_api_key

    @property
    @abc.abstractmethod
    def staging_dir(self) -> Path:
        """
        Path to the staging directory.

        Staging directory handles all the files that belong to this deployment.
        This directory is supplied by the user, which should represent an isolated deployment environment.
        The reason we need a staging directory, is that we want to reproduce the docker effect of an isolated
        workspace, where each directory within this parent is a volume which can be cleaned up, giving us a
        volume inventory when running tests.

        Staging dir will have the following structure. When tearing down and removing everything, will
        remove all directories with the appropriate resource prefix.
        ```
        /path/to/original/win-dist/             # we won't try to touch this. (windows only)
        /path/to/staging_dir/
            |- {resource_prefix}win-dist/       # windows only
                |- lib/
                |- locales/
                |- public/
                |- runtime/
                |- script/
                |- templates/
                |- lrr.conf
                |- package.json
                |- run.ps1
            |- {resource_prefix}archives/       # holds LRR archives
            |- {resource_prefix}thumb/          # holds LRR thumbnails
            |- {resource_prefix}temp/           # move temp out to avoid applying unnecessary Shinobu pressure (windows only)
            |- {resource_prefix}redis/          # dedicated redis directory (instead of using contents). Windows only
            |- {resource_prefix}log/            # holds LRR logs
                |- lanraragi.log
                |- redis.log
            |- {resource_prefix}pid/            # holds PID files (windows only)
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
    @abc.abstractmethod
    def archives_dir(self) -> Path:
        """
        Path to the archives directory.
        """

    @property
    @abc.abstractmethod
    def logs_dir(self) -> Path:
        """
        Path to the logs directory.
        """

    @property
    def lanraragi_logs_path(self) -> Path:
        return self.logs_dir / "lanraragi.log"

    @property
    def shinobu_logs_path(self) -> Path:
        return self.logs_dir / "shinobu.log"

    @property
    def mojo_logs_path(self) -> Path:
        return self.logs_dir / "mojo.log"

    @property
    def redis_dir(self) -> Path:
        """
        Path to Redis database directory.
        """
        redis_dirname = self.resource_prefix + "redis"
        return self.staging_dir / redis_dirname

    @property
    def redis_client(self) -> redis.Redis:
        """
        Redis client for this LRR deployment
        """
        if not hasattr(self, "_redis_client") or not self._redis_client:
            self._redis_client = redis.Redis(host="127.0.0.1", port=self.redis_port, decode_responses=True)
        return self._redis_client

    @property
    def plugin_paths(self) -> PluginPathsT:
        """
        Test-time third party plugins
        """
        if not hasattr(self, "_plugin_paths"):
            self._plugin_paths = {}
        return self._plugin_paths

    @plugin_paths.setter
    def plugin_paths(self, plugin_paths: PluginPathsT):
        self._plugin_paths = plugin_paths

    @abc.abstractmethod
    def setup(
        self, with_api_key: bool=False, with_nofunmode: bool=False, enable_cors: bool=False, lrr_debug_mode: bool=False,
        environment: dict[str, str]={}, plugin_paths: PluginPathsT={},
        test_connection_max_retries: int=4
    ):
        """
        Main entrypoint to setting up a LRR environment.

        Performs all setup logic for a requirement, including creating directories and files, volumes, networks,
        and other resources required for a working LRR environment.

        Does not, and will not perform any cleanup function. Use teardown or another API for that logic.

        Args:
            `with_api_key`: whether to add an API key (default API key: "lanraragi") to the LRR environment
            `with_nofunmode`: whether to enable nofunmode in the LRR environment
            `enable_cors`: whether to enable CORS headers for the Client API
            `lrr_debug_mode`: whether to enable debug mode for the LRR application
            `environment`: additional environment variables map to pass through to LRR during startup time
            `plugin_paths`: a list of (absolute) plugin paths by plugin type.
                If plugins are provided: copies the assigned plugins over to the target directory.
            `test_connection_max_retries`: Number of attempts to connect to the LRR server. Usually resolves after 2, unless there are many files.
        """

    @abc.abstractmethod
    def start(self, test_connection_max_retries: int=4):
        """
        Start an existing deployment.
        """

    @abc.abstractmethod
    def stop(self):
        """
        Stop an existing deployment.
        """

    @abc.abstractmethod
    def restart(self):
        """
        Restart the deployment (does not remove data), and ensures the LRR server is running.
        """

    @abc.abstractmethod
    def teardown(self, remove_data: bool=False):
        """
        Main entrypoint to removing a LRR installation and cleaning up data.

        Args:
            `remove_data`: whether to remove the data associated with the LRR environment,
            such as logs, archives, and cache.
        """

    @abc.abstractmethod
    def start_lrr(self):
        """
        Start the LRR server.
        """

    @abc.abstractmethod
    def start_redis(self):
        """
        Start the Redis server.
        """

    @abc.abstractmethod
    def stop_lrr(self, timeout: int=10):
        """
        Stop the LRR server

        Args:
            `timeout`: timeout in seconds.
        """

    @abc.abstractmethod
    def stop_redis(self, timeout: int=10):
        """
        Stop the Redis server

        Args:
            `timeout`: timeout in seconds.
        """

    @abc.abstractmethod
    def get_lrr_logs(self, tail: int=100) -> bytes:
        """
        Get logs as bytes.

        Args:
            `tail`: max number of lines to keep from last line.
        """

    @abc.abstractmethod
    def get_redis_logs(self, tail: int=100) -> bytes:
        """
        Get Redis logs as bytes.

        Args:
            `tail`: max number of lines to keep from last line.
        """

    @abc.abstractmethod
    def apply_plugins(self):
        """
        Applies plugins to the LRR target plugins directory.
        """

    def get_redis_backup_dir(self, backup_id: str) -> Path:
        backup_dirname = self.resource_prefix + f"redis_backup_{backup_id}"
        backup_dir = self.staging_dir / backup_dirname
        return backup_dir

    def backup_redis_data(self, backup_id: str) -> Path:
        """
        Make a copy of the current redis database (for benchmarking purposes).
        Returns the path to the backup directory.

        Redis DB should probably be down.
        """
        backup_dir = self.get_redis_backup_dir(backup_id)
        if backup_dir.exists():
            self.logger.info(f"Removing existing backup: {backup_dir}")
            shutil.rmtree(backup_dir)
        shutil.copy2(self.redis_dir, backup_dir)
        self.logger.debug(f"Backup {backup_id} OK")
        return backup_dir

    def restore_redis_backup(self, backup_id: str):
        """
        Restore from redis backup (for benchmarking purposes). Ensure that redis is shutdown.
        """
        try:
            self.redis_client.ping()
            raise DeploymentException(f"Cannot restore from backup {backup_id} while database is live!")
        except redis.exceptions.ConnectionError:
            pass
        backup_dir = self.get_redis_backup_dir(backup_id)
        if self.redis_dir.exists():
            self.logger.info(f"Removing existing database info: {self.redis_dir}")
            shutil.rmtree(self.redis_dir)
        shutil.copy2(backup_dir, self.redis_dir)
        self.logger.debug(f"Restore from backup {backup_id} OK")

    def update_api_key(self, api_key: str | None):
        """
        Insert/update LRR API key (or remove if None is passed).
        """
        self.lrr_api_key = api_key
        self.redis_client.select(2)
        if api_key is None:
            self.redis_client.hdel("LRR_CONFIG", "apikey")
        else:
            self.redis_client.hset("LRR_CONFIG", "apikey", api_key)

    def enable_nofun_mode(self):
        """
        Enable nofun mode.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "nofunmode", "1")

    def disable_nofun_mode(self):
        """
        Disable nofun mode.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "nofunmode", "0")

    def enable_lrr_debug_mode(self):
        """
        Enable debug logs.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "enable_devmode", "1")

    def disable_lrr_debug_mode(self):
        """
        Disable debug logs.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "enable_devmode", "0")

    def enable_cors(self):
        """
        Enable CORS.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "enablecors", "1")

    def disable_cors(self):
        """
        Disable CORS.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "enablecors", "0")

    def enable_auth_progress(self):
        """
        Enable server-side progress tracking only for authenticated requests.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "authprogress", "1")

    def disable_auth_progress(self):
        """
        Disable authenticated-only requirement for server-side progress tracking.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "authprogress", "0")

    def enable_openapi_bypass(self):
        """
        Disable OpenAPI request/response validation (bypass mode).
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "disableopenapi", "1")

    def disable_openapi_bypass(self):
        """
        Enable OpenAPI request/response validation (normal mode).
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "disableopenapi", "0")

    def enable_local_progress(self):
        """
        Enable local (client-side) progress tracking, disabling server-side progress tracking.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "localprogress", "1")

    def disable_local_progress(self):
        """
        Disable local (client-side) progress tracking, re-enabling server-side progress tracking.
        """
        self.redis_client.select(2)
        self.redis_client.hset("LRR_CONFIG", "localprogress", "0")

    def test_lrr_connection(self, port: int, test_connection_max_retries: int=4):
        """
        Test the LRR connection with retry and exponential backoff.
        If connection is not established by then, teardown the deployment completely and raise an exception.

        Args:
            `test_connection_max_retries`: max number of retries before throwing a `DeploymentException`.
        """
        retry_count = 0
        while True:
            try:
                resp = requests.get(f"http://127.0.0.1:{port}")
                if resp.status_code != 200:
                    self.teardown(remove_data=True)
                    raise DeploymentException(f"Response status code is not 200: {resp.status_code}")
                else:
                    break
            except requests.exceptions.ConnectionError:
                if retry_count < test_connection_max_retries:
                    time_to_sleep = 2 ** (retry_count + 1)

                    if retry_count < test_connection_max_retries-3:
                        self.logger.debug(f"Could not reach LRR server ({retry_count+1}/{test_connection_max_retries}); retrying after {time_to_sleep}s.")
                    elif retry_count < test_connection_max_retries-2:
                        self.logger.info(f"Could not reach LRR server ({retry_count+1}/{test_connection_max_retries}); retrying after {time_to_sleep}s.")
                    elif retry_count < test_connection_max_retries-1:
                        self.logger.warning(f"Could not reach LRR server ({retry_count+1}/{test_connection_max_retries}); retrying after {time_to_sleep}s.")
                    retry_count += 1
                    time.sleep(time_to_sleep)
                    continue
                else:
                    self.logger.error("Failed to connect to LRR server! Dumping logs and shutting down server.")
                    self.display_lrr_logs()
                    self.teardown(remove_data=True)
                    raise DeploymentException("Failed to connect to the LRR server!")

    def test_redis_connection(self, max_retries: int=4):
        self.logger.debug("Connecting to Redis...")
        retry_count = 0
        while True:
            try:
                self.redis_client.ping()
                break
            except redis.exceptions.ConnectionError:
                if retry_count >= max_retries:
                    self.logger.error("Failed to connect to Redis! Dumping Redis logs...")
                    self.display_redis_logs()
                    raise
                time_to_sleep = 2 ** (retry_count + 1)
                self.logger.warning(f"Failed to connect to Redis. Retry in {time_to_sleep}s ({retry_count+1}/{max_retries})...")
                retry_count += 1
                time.sleep(time_to_sleep)

    def display_lrr_logs(self, tail: int=100, log_level: int=logging.ERROR):
        """
        Display LRR logs to (error) output, used for debugging.
        This is still used by containers, in case log files do not exist.

        Args:
            tail: show up to how many lines from the last output
            log_level: integer value level of log (see logging module)
        """
        lrr_logs = self.get_lrr_logs(tail=tail)
        if lrr_logs:
            log_text = lrr_logs.decode('utf-8', errors='replace')
            for line in log_text.split('\n'):
                if line.strip():
                    self.logger.log(log_level, f"LRR: {line}")

    def display_redis_logs(self, tail: int=100, log_level: int=logging.ERROR):
        """
        Display Redis logs to (error) output, used for debugging.

        Args:
            tail: show up to how many lines from the last output
            log_level: integer value level of log (see logging module)
        """
        redis_logs = self.get_redis_logs(tail=tail)
        if redis_logs:
            log_text = redis_logs.decode('utf-8', errors='replace')
            for line in log_text.split('\n'):
                if line.strip():
                    self.logger.log(log_level, f"Redis: {line}")

    def _read_log_with_rotated_gzip(self, base_filename: str) -> str:
        """
        Read a log file and all compressed rotated variants in ascending rotation order.

        Order:
        - <base_filename>
        - <base_filename>.1.gz
        - <base_filename>.2.gz
        - ...
        """
        parts: list[str] = []
        log_path = self.logs_dir / base_filename
        if log_path.exists():
            with open(log_path) as f:
                parts.append(f.read())

        rotated_logs = list(self.logs_dir.glob(f"{base_filename}.*.gz"))

        def parse_index(path: Path) -> int:
            name = path.name
            # expected format: <base_filename>.<idx>.gz
            idx_str = name.split(".")[-2]
            return int(idx_str)

        for gz_path in sorted(rotated_logs, key=parse_index):
            with gzip.open(gz_path, mode="rt", encoding="utf-8", errors="replace") as f:
                parts.append(f.read())

        return "".join(parts)

    def read_lrr_logs(self) -> str:
        """
        Read all lanraragi.log logs, including rotated ones, as a single string, in the order:

        - lanraragi.log
        - lanraragi.log.1.gz
        - lanraragi.log.2.gz
        - ...
        """
        return self._read_log_with_rotated_gzip("lanraragi.log")

    def read_mojo_logs(self) -> str:
        """
        Read all mojo.log logs, including rotated ones, as a single string, in the order:

        - mojo.log
        - mojo.log.1.gz
        - mojo.log.2.gz
        - ...
        """
        return self._read_log_with_rotated_gzip("mojo.log")

    def read_log(self, log_file: str) -> str:
        """
        Read a log file from logs directory.
        """
        with open(log_file) as f:
            return f.read()

    def lrr_client(
        self, ssl: bool=True,
        client_session: aiohttp.ClientSession | None=None, connector: aiohttp.BaseConnector | None=None,
        logger: logging.Logger | None=None
    ) -> LRRClient:
        """
        Returns a LRRClient object configured to connect to this server.
        """
        return LRRClient(self.lrr_base_url, self.lrr_api_key, ssl=ssl, client_session=client_session, connector=connector, logger=logger)

def expect_no_error_logs(environment: AbstractLRRDeploymentContext, logger: logging.Logger):
    """
    Assert no logs with error level severity in LRR and Shinobu.
    """
    for event in parse_lrr_logs(environment.read_lrr_logs()):
        assert event.severity_level != 'error', "LANraragi process emitted error logs."

    if environment.shinobu_logs_path.exists():
        for event in parse_lrr_logs(environment.read_log(environment.shinobu_logs_path)):
            assert event.severity_level != 'error', "Shinobu process emitted error logs."
    else:
        logger.warning("No shinobu logs found.")
