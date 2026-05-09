"""
Metrics exporter integration tests.

Metrics requires enablemetrics + server restart, so each test manages its own environment lifecycle.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def resource_prefix(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    yield request.config.getoption("--resource-prefix") + "test_"


@pytest.fixture
def port_offset(request: pytest.FixtureRequest) -> Generator[int, None, None]:
    yield request.config.getoption("--port-offset") + 10


@pytest.fixture
def environment(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int) -> Generator[AbstractLRRDeploymentContext, None, None]:
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)

    try:
        environment.setup(with_api_key=True, lrr_debug_mode=is_lrr_debug_mode)
        environment.enable_metrics()
        environment.restart()
        request.session.lrr_environments = {resource_prefix: environment}
        yield environment
    finally:
        environment.teardown(remove_data=True)


@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_metrics_endpoint(lrr_client: LRRClient):
    """
    Test metrics endpoint returns valid Prometheus exposition data after API activity.

    1. Verify connection and make API calls to generate metrics.
    2. Wait for flush interval, then make a throwaway call to trigger flush.
    3. Fetch metrics and verify expected metric families are present.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> GENERATE METRICS >>>>>
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    del response, error

    # wait for >REQUEST_METRICS_FLUSH_INTERVAL (1s, lib/LANraragi/Model/Metrics.pm)
    # so the throwaway call's after_dispatch hook performs an opportunistic flush
    await asyncio.sleep(2)
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Throwaway call failed (status {error.status}): {error.error}"
    del response, error
    # <<<<< GENERATE METRICS <<<<<

    # >>>>> FETCH AND VERIFY METRICS >>>>>
    response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get metrics (status {error.status}): {error.error}"

    content = response.content
    assert "# EOF" in content, "Metrics response missing EOF marker"
    assert "lanraragi_archives_total" in content, "Missing lanraragi_archives_total metric"
    assert "lanraragi_server_info" in content, "Missing lanraragi_server_info metric"
    assert "lanraragi_pages_read_total" in content, "Missing lanraragi_pages_read_total metric"
    assert "lanraragi_api_requests_total" in content, "Missing lanraragi_api_requests_total metric"
    # <<<<< FETCH AND VERIFY METRICS <<<<<


@pytest.mark.asyncio
async def test_metrics_endpoint_nofun(environment: AbstractLRRDeploymentContext, lrr_client: LRRClient):
    """
    Test metrics endpoint is reachable with API key auth when nofun mode is enabled.

    1. Enable nofun mode and restart.
    2. Fetch metrics with API key, verify 200 response with expected content.
    """
    environment.enable_nofun_mode()
    environment.restart()

    response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get metrics (status {error.status}): {error.error}"

    content = response.content
    assert "# EOF" in content, "Metrics response missing EOF marker"
    assert "lanraragi_nofun_mode 1" in content, "Missing or incorrect lanraragi_nofun_mode metric"
