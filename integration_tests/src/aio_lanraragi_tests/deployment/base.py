import abc
import logging
import time
from typing import Optional

from aio_lanraragi_tests.common import DEFAULT_LRR_PORT, DEFAULT_REDIS_PORT
from aio_lanraragi_tests.exceptions import DeploymentException
import aiohttp
import requests

from lanraragi.clients.client import LRRClient


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
    def lrr_api_key(self) -> Optional[str]:
        """
        Unencoded API key for this instance.
        Can be None, which represents a server without an API key.
        """
        if not hasattr(self, "_lrr_api_key"):
            return None
        return self._lrr_api_key

    @lrr_api_key.setter
    def lrr_api_key(self, lrr_api_key: Optional[str]):
        self._lrr_api_key = lrr_api_key

    @abc.abstractmethod
    def update_api_key(self, api_key: Optional[str]):
        """
        Add an API key to the LRR environment.

        Args:
            `api_key`: API key to add to redis. If set to None, will remove it from the database.
        """
        ...

    @abc.abstractmethod
    def enable_nofun_mode(self):
        ...

    @abc.abstractmethod
    def disable_nofun_mode(self):
        ...

    @abc.abstractmethod
    def enable_lrr_debug_mode(self):
        ...
    
    @abc.abstractmethod
    def disable_lrr_debug_mode(self):
        ...

    @abc.abstractmethod
    def setup(
        self, with_api_key: bool=False, with_nofunmode: bool=False, lrr_debug_mode: bool=False,
        test_connection_max_retries: int=4
    ):
        """
        Main entrypoint to setting up a LRR environment.

        Args:
            `with_api_key`: whether to add an API key (default API key: "lanraragi") to the LRR environment
            `with_nofunmode`: whether to enable nofunmode in the LRR environment
            `lrr_debug_mode`: whether to enable debug mode for the LRR application
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

    def display_lrr_logs(self, tail: int=100, log_level: int=logging.ERROR):
        """
        Display LRR logs to (error) output, used for debugging.

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

    def lrr_client(
        self, ssl: bool=True, 
        client_session: Optional[aiohttp.ClientSession]=None, connector: Optional[aiohttp.BaseConnector]=None, 
        logger: Optional[logging.Logger]=None
    ) -> LRRClient:
        """
        Returns a LRRClient object configured to connect to this server.
        """
        return LRRClient(self.lrr_base_url, self.lrr_api_key, ssl=ssl, client_session=client_session, connector=connector, logger=logger)