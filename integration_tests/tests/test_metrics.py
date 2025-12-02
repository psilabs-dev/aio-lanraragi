"""
Test metrics-related functionality.
"""

import asyncio
import logging
from pathlib import Path
import sys
import tempfile
from typing import Dict, Generator
import pytest
import numpy as np
import pytest_asyncio

from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import GetArchiveCategoriesRequest, GetArchiveTankoubonsRequest, GetArchiveThumbnailRequest

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.helpers import expect_no_error_logs, get_bounded_sem, save_archives, upload_archives

LOGGER = logging.getLogger(__name__)
ENABLE_SYNC_FALLBACK = False # for debugging.

def get_api_call_count(metrics, endpoint: str, method: str = "GET") -> int:
    """Extract the count for a specific API endpoint from Prometheus metrics."""
    for family in metrics:
        if family.name == 'lanraragi_api_requests':
            for sample in family.samples:
                labels = sample.labels
                if labels.get('endpoint') == endpoint and labels.get('method') == method:
                    return int(sample.value)
    return 0

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    environment.setup(with_api_key=True, with_nofunmode=False, enable_metrics=True, lrr_debug_mode=is_lrr_debug_mode)

    # configure environments to session
    environments: Dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

    yield environment
    environment.teardown(remove_data=True)

@pytest.fixture
def npgenerator(request: pytest.FixtureRequest) -> Generator[np.random.Generator, None, None]:
    seed: int = int(request.config.getoption("npseed"))
    generator = np.random.default_rng(seed)
    yield generator

@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield get_bounded_sem()

@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) ->  Generator[LRRClient, None, None]:
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()

@pytest.mark.skipif(sys.platform != "win32", reason="Cache priming required only for flaky Windows testing environments.")
@pytest.mark.asyncio
@pytest.mark.xfail
async def test_xfail_catch_flakes(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    This xfail test case serves no integration testing purpose, other than to prime the cache of flaky testing hosts
    and reduce the chances of subsequent test case failures caused by network flakes, such as remote host connection
    closures or connection refused errors resulting from high client request pressure to unprepared host.

    Therefore, occasional test case failures here are expected and ignored.
    """
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    LOGGER.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    assert not any(environment.archives_dir.iterdir()), "Archive directory is not empty!"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, npgenerator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        LOGGER.debug("Uploading archives to server.")
        await upload_archives(write_responses, npgenerator, semaphore, lrr_client, force_sync=ENABLE_SYNC_FALLBACK)
    # <<<<< UPLOAD STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.asyncio
async def test_metrics(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator):
    """
    Test metrics collection for specific archive APIs.
    For each API, make the API call then verify it was recorded in metrics.
    """
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, npgenerator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        LOGGER.debug("Uploading archives to server.")
        await upload_archives(write_responses, npgenerator, semaphore, lrr_client, force_sync=ENABLE_SYNC_FALLBACK)
    # <<<<< UPLOAD STAGE <<<<<

    # Get an archive ID for testing APIs that require one
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    first_arcid = response.data[0].arcid
    assert first_arcid, "No archives uploaded, cannot test APIs that require arcid"

    # >>>>> TEST API METRICS COLLECTION >>>>>
    LOGGER.debug("Testing metrics collection for archive APIs.")

    # get_all_archives
    LOGGER.debug("Testing get_all_archives metrics collection")
    await asyncio.sleep(5)
    initial_response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get initial metrics (status {error.status}): {error.error}"
    initial_metrics_list = list(initial_response.metrics)
    initial_count = get_api_call_count(initial_metrics_list, "/api/archives", "GET")
    _, _ = await lrr_client.archive_api.get_all_archives()
    response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get metrics after get_all_archives (status {error.status}): {error.error}"
    new_metrics_list = list(response.metrics)
    new_count = get_api_call_count(new_metrics_list, "/api/archives", "GET")
    assert new_count > initial_count, f"get_all_archives call not recorded in metrics: {initial_count} -> {new_count}"

    # get_untagged_archives
    LOGGER.debug("Testing get_untagged_archives metrics collection")
    initial_response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get initial metrics (status {error.status}): {error.error}"
    initial_count = get_api_call_count(list(initial_response.metrics), "/api/archives/untagged", "GET")
    _, _ = await lrr_client.archive_api.get_untagged_archives()
    response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get metrics after get_untagged_archives (status {error.status}): {error.error}"
    new_count = get_api_call_count(list(response.metrics), "/api/archives/untagged", "GET")
    assert new_count > initial_count, f"get_untagged_archives call not recorded in metrics: {initial_count} -> {new_count}"

    # get_archive_categories
    LOGGER.debug("Testing get_archive_categories metrics collection")
    initial_response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get initial metrics (status {error.status}): {error.error}"
    initial_count = get_api_call_count(list(initial_response.metrics), "/api/archives/:id/categories", "GET")
    _, _ = await lrr_client.archive_api.get_archive_categories(GetArchiveCategoriesRequest(arcid=first_arcid))
    response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get metrics after get_archive_categories (status {error.status}): {error.error}"
    new_count = get_api_call_count(list(response.metrics), "/api/archives/:id/categories", "GET")
    assert new_count > initial_count, f"get_archive_categories call not recorded in metrics: {initial_count} -> {new_count}"

    # get_archive_tankoubons
    LOGGER.debug("Testing get_archive_tankoubons metrics collection")
    initial_response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get initial metrics (status {error.status}): {error.error}"
    initial_count = get_api_call_count(list(initial_response.metrics), "/api/archives/:id/tankoubons", "GET")
    _, _ = await lrr_client.archive_api.get_archive_tankoubons(GetArchiveTankoubonsRequest(arcid=first_arcid))
    response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get metrics after get_archive_tankoubons (status {error.status}): {error.error}"
    new_count = get_api_call_count(list(response.metrics), "/api/archives/:id/tankoubons", "GET")
    assert new_count > initial_count, f"get_archive_tankoubons call not recorded in metrics: {initial_count} -> {new_count}"

    # get_archive_thumbnail
    LOGGER.debug("Testing get_archive_thumbnail metrics collection")
    initial_response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get initial metrics (status {error.status}): {error.error}"
    initial_count = get_api_call_count(list(initial_response.metrics), "/api/archives/:id/thumbnail", "GET")
    _, _ = await lrr_client.archive_api.get_archive_thumbnail(GetArchiveThumbnailRequest(arcid=first_arcid))
    response, error = await lrr_client.metrics_api.get_metrics()
    assert not error, f"Failed to get metrics after get_archive_thumbnail (status {error.status}): {error.error}"
    new_count = get_api_call_count(list(response.metrics), "/api/archives/:id/thumbnail", "GET")
    assert new_count > initial_count, f"get_archive_thumbnail call not recorded in metrics: {initial_count} -> {new_count}"
    # <<<<< TEST API METRICS COLLECTION <<<<<
