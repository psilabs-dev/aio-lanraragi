import abc
import logging


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
    def setup(self, test_connection_max_retries: int=4):
        """
        Main entrypoint to setting up a LRR environment.
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
