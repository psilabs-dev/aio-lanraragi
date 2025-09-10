import abc
import logging
import time

from aio_lanraragi_tests.exceptions import DeploymentException
import requests


class AbstractLRRDeploymentContext(abc.ABC):

    @abc.abstractmethod
    def get_logger(self) -> logging.Logger:
        ...

    @abc.abstractmethod
    def add_api_key(self, api_key: str):
        ...

    @abc.abstractmethod
    def enable_nofun_mode(self):
        ...

    @abc.abstractmethod
    def disable_nofun_mode(self):
        ...

    @abc.abstractmethod
    def setup(
        self, with_api_key: bool=False, with_nofunmode: bool=False,
        test_connection_max_retries: int=4
    ):
        """
        Main entrypoint to setting up a LRR environment.

        Args:
            with_api_key: whether to add an API key (default API key: "lanraragi") to the LRR environment
            with_nofunmode: whether to enable nofunmode in the LRR environment
            test_connection_max_retries: Number of attempts to connect to the LRR server. Usually resolves after 2, unless there are many files.
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
            remove_data: whether to remove the data associated with the LRR environment
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
        Stop the LRR server (timeout in s)
        """
    
    @abc.abstractmethod
    def stop_redis(self, timeout: int=10):
        """
        Stop the Redis server (timeout in s)
        """

    @abc.abstractmethod
    def get_lrr_logs(self, tail: int=100) -> bytes:
        """
        Get logs as bytes.
        """

    def test_lrr_connection(self, test_connection_max_retries: int=4):
        """
        Test the LRR connection with retry and exponential backoff.
        If connection is not established by then, teardown the deployment completely and raise an exception.
        """
        retry_count = 0
        while True:
            try:
                resp = requests.get(f"http://127.0.0.1:{self.lrr_port}")
                if resp.status_code != 200:
                    self.teardown(remove_data=True)
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
                    self.get_logger().log(log_level, f"LRR: {line}")
