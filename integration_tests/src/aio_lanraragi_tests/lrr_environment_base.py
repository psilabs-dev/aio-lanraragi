import abc


class AbstractLRREnvironment(abc.ABC):

    @abc.abstractmethod
    def setup(self, test_connection_max_retries: int=4):
        """
        Main entrypoint to setting up a LRR environment.
        """
    
    @abc.abstractmethod
    def teardown(self):
        """
        Main entrypoint to cleaning up and removing a LRR installation.
        """

    @abc.abstractmethod
    def display_lrr_logs(self):
        """
        Debugging method.
        """
