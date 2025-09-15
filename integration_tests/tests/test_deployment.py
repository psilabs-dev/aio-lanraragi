import logging
from aio_lanraragi_tests.common import is_port_available
from aio_lanraragi_tests.deployment.factory import generate_deployment
import pytest

logger = logging.getLogger(__name__)

# @pytest.mark.experimental
def test_deployment(request: pytest.FixtureRequest):
    """
    Tests multiple deployments.
    """
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    env_1 = generate_deployment(request, "test_1_", 10)
    env_2 = generate_deployment(request, "test_2_", 11)

    try:

        assert is_port_available(env_1.lrr_port), f"Port {env_1.lrr_port} should be available!"
        assert is_port_available(env_2.lrr_port), f"Port {env_2.lrr_port} should be available!"

        env_1.setup(lrr_debug_mode=is_lrr_debug_mode)
        assert not is_port_available(env_1.lrr_port), f"Port {env_1.lrr_port} should not be available!"

        env_2.setup(lrr_debug_mode=is_lrr_debug_mode)
        assert not is_port_available(env_2.lrr_port), f"Port {env_2.lrr_port} should not be available!"

        env_1.stop()
        assert is_port_available(env_1.lrr_port), f"Port {env_1.lrr_port} should be available!"

        env_2.stop()
        assert is_port_available(env_2.lrr_port), f"Port {env_2.lrr_port} should be available!"

        env_1.start()
        assert not is_port_available(env_1.lrr_port), f"Port {env_1.lrr_port} should not be available!"
    finally:
        env_1.teardown(remove_data=True)
        env_2.teardown(remove_data=True)
