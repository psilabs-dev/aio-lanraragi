from typing import override
from aio_lanraragi_tests.lrr_environment_base import AbstractLRREnvironment


class LRRWindowsEnvironment(AbstractLRREnvironment):

    @override
    def setup(self, test_connection_max_retries: int = 4):
        raise NotImplementedError

    @override
    def teardown(self):
        raise NotImplementedError

    @override
    def display_lrr_logs(self):
        raise NotImplementedError
