from aio_lanraragi_tests.common import is_port_available
from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
import pytest

def test_two_deployment_toggling(request: pytest.FixtureRequest):
    """
    Tests bringing two deployments up and down.
    """
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    env_1 = generate_deployment(request, "test_1_", 10)
    env_2 = generate_deployment(request, "test_2_", 11)

    if isinstance(env_1, DockerLRRDeploymentContext):
        # TODO: in a dockerized deployment, asserting that a stopped container must be followed by
        # port availability is too strict of a condition, because the docker daemon may reserve
        # the port and may not free it until underlying network configurations are updated, which
        # can take up to a minute. See:
        # 
        # - https://docs.docker.com/engine/network/packet-filtering-firewalls
        # - https://stackoverflow.com/questions/63467759/close-docker-port-when-container-is-stopped
        # 
        # Also, the whole point of this is to ensure that starting/stopping the environment *does*
        # indeed start/stop the env as advertised. Trying to check this with a docker environment
        # (a tool designed for orchestration) is a rather useless exercise to do imo.
        pytest.skip("Port availability condition too strict in Docker environment.")

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

@pytest.mark.asyncio
async def test_two_deployment_basic_api(request: pytest.FixtureRequest):
    """
    Test to establish basic API connections to multiple LRR instances.
    This test should confirm that the two instances running are healthy.
    """
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    env_1 = generate_deployment(request, "test_1_", 10)
    env_2 = generate_deployment(request, "test_2_", 11)

    try:
        env_1.setup(lrr_debug_mode=is_lrr_debug_mode, with_api_key=True)
        env_2.setup(lrr_debug_mode=is_lrr_debug_mode, with_api_key=True)

        async with (
            env_1.lrr_client() as lrr_1,
            env_2.lrr_client() as lrr_2,
        ):
            for lrr in [lrr_1, lrr_2]:
                _, error = await lrr.archive_api.get_all_archives()
                assert not error, f"Failed to get all archives on address {lrr.lrr_base_url} (status {error.status}): {error.error}"
                _, error = await lrr.shinobu_api.get_shinobu_status()
                assert not error, f"Failed to get shinobu status on address {lrr.lrr_base_url} (status {error.status}): {error.error}"

    finally:
        env_1.teardown(remove_data=True)
        env_2.teardown(remove_data=True)
