import logging

import pytest

from aio_lanraragi_tests.common import is_port_available
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment

LOGGER = logging.getLogger(__name__)

def test_two_deployment_toggling(request: pytest.FixtureRequest):
    """
    Tests bringing two deployments up and down.
    """
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")

    prefix_1 = 'test_1'
    prefix_2 = 'test_2'
    env_1 = generate_deployment(request, prefix_1, 10, logger=LOGGER)
    env_2 = generate_deployment(request, prefix_2, 11, logger=LOGGER)

    # configure environments to session
    environments: dict[str, AbstractLRRDeploymentContext] = {
        prefix_1: env_1,
        prefix_2: env_2
    }
    request.session.lrr_environments = environments

    if isinstance(env_1, DockerLRRDeploymentContext):
        # see DockerLRRDeploymentContext.stop documentation.
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

        # check logs for errors
        expect_no_error_logs(env_1, LOGGER)
        expect_no_error_logs(env_2, LOGGER)
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
            env_1.lrr_client() as lrr_client_1,
            env_2.lrr_client() as lrr_client_2,
        ):
            for lrr in [lrr_client_1, lrr_client_2]:
                _, error = await lrr.archive_api.get_all_archives()
                assert not error, f"Failed to get all archives on address {lrr.lrr_base_url} (status {error.status}): {error.error}"
                _, error = await lrr.shinobu_api.get_shinobu_status()
                assert not error, f"Failed to get shinobu status on address {lrr.lrr_base_url} (status {error.status}): {error.error}"

        # check logs for errors
        expect_no_error_logs(env_1, LOGGER)
        expect_no_error_logs(env_2, LOGGER)
    finally:
        env_1.teardown(remove_data=True)
        env_2.teardown(remove_data=True)

# # TODO: remove when docker deployment usage of redis.conf is confirmed/stable.
# # this test exists as reference to ensure that Docker redis instance uses LRR redis.conf file.
# def test_redis_conf_applied(request: pytest.FixtureRequest):
#     """
#     Smoke test: verifies that the Redis container applies LRR's redis.conf
#     settings (appendonly, save thresholds) for all Docker deployment scenarios
#     (--build, --git-url, --image).
#     """
#     env = generate_deployment(request, "test_redis_conf_", 10, logger=LOGGER)

#     if not isinstance(env, DockerLRRDeploymentContext):
#         pytest.skip("Redis conf smoke test only applies to Docker deployments.")

#     request.session.lrr_environments = {"test_redis_conf_": env}

#     try:
#         env.setup()

#         appendonly = env.redis_client.config_get('appendonly')
#         assert appendonly.get('appendonly') == 'yes', \
#             f"Expected appendonly=yes per LRR redis.conf, got: {appendonly}"

#         save = env.redis_client.config_get('save')
#         assert save.get('save') == '900 1 60 500', \
#             f"Expected save=900 1 60 500 per LRR redis.conf, got: {save}"
#     finally:
#         env.teardown(remove_data=True)
