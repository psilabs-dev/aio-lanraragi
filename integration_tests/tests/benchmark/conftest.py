import logging
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.benchmarking import BenchmarkCollector
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
from tests.conftest import _sanitize_nodeid, _save_server_logs

LOGGER = logging.getLogger(__name__)

@pytest.fixture(scope="session")
def benchmark_collector(request: pytest.FixtureRequest) -> Generator[BenchmarkCollector, None, None]:
    label = request.config.getoption("--benchmark-label") or "unlabeled"
    collector = BenchmarkCollector(label=label)
    yield collector
    output_path = request.config.getoption("--benchmark-output")
    if output_path:
        collector.write(Path(output_path))

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    """
    Reserved resource prefix for pytest benchmarking.
    """
    yield "bench_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    """
    Reserved port offset for pytest benchmarking.
    """
    yield 20

@pytest.fixture
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    """
    Yield an environment that is prepared for benchmarking pressure.
    """
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)

    # Wrap test_redis_connection to disable RDB snapshot write protection
    # immediately after Redis becomes reachable, before any config writes.
    # High-volume benchmark uploads can exhaust container disk, causing
    # Redis to enter MISCONF state and reject all writes.
    original_test_redis = None
    if isinstance(environment, DockerLRRDeploymentContext):
        original_test_redis = environment.test_redis_connection
        def _test_redis_then_disable_bgsave_error(*args, **kwargs):
            original_test_redis(*args, **kwargs)
            environment._exec_redis_cli("CONFIG SET stop-writes-on-bgsave-error no")
        environment.test_redis_connection = _test_redis_then_disable_bgsave_error

    try:
        environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)

        environments: dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
        request.session.lrr_environments = environments

        yield environment
    finally:
        if original_test_redis is not None:
            environment.test_redis_connection = original_test_redis
        environment.teardown(remove_data=True)

@pytest.fixture
def npgenerator(request: pytest.FixtureRequest) -> Generator[np.random.Generator, None, None]:
    seed: int = int(request.config.getoption("npseed"))
    generator = np.random.default_rng(seed)
    yield generator

@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
    """Provides a LRRClient for testing with async cleanup."""
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    """Save server logs on both pass and failure for benchmark tests."""

    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    if report.when == "call":
        server_logs_dir = item.config.getoption("--server-logs")
        if not server_logs_dir:
            return
        server_logs_dir = Path(server_logs_dir)
        try:
            if hasattr(item.session, 'lrr_environments') and item.session.lrr_environments:
                environments_by_prefix: dict[str, AbstractLRRDeploymentContext] = item.session.lrr_environments
                _save_server_logs(server_logs_dir, item.nodeid, environments_by_prefix)
                LOGGER.info(f"Benchmark server logs saved to {server_logs_dir / _sanitize_nodeid(item.nodeid)}")
        except Exception as e:  # noqa: BLE001
            LOGGER.error(f"Failed to save benchmark server logs: {e}")
