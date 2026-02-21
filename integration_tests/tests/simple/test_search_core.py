"""
Any integration test which tests the correctness of the search API.
"""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.search import GetRandomArchivesRequest, SearchArchiveIndexRequest

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import save_archives, upload_archives
from aio_lanraragi_tests.utils.flakes import xfail_catch_flakes_inner

LOGGER = logging.getLogger(__name__)
ENABLE_SYNC_FALLBACK = False # for debugging.

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
    await xfail_catch_flakes_inner(lrr_client, semaphore, environment, num_archives=100, npgenerator=npgenerator)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_search_api(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    Very basic functional test of the search API.
    
    1. upload 100 archives
    2. search for 20 archives using the search API
    3. search for 20 archives using random search API
    4. search for 20 archives using random search API with newonly=true
    5. search for 20 archives using random search API with untaggedonly=true (should return empty)
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

    # >>>>> SEARCH STAGE >>>>>
    # TODO: current test design limits ability to test results of search (e.g. tag filtering), will need to unravel logic for better test transparency
    response, error = await lrr_client.search_api.search_archive_index(SearchArchiveIndexRequest())
    assert not error, f"Failed to search archive index (status {error.status}): {error.error}"
    assert len(response.data) == 100
    del response, error

    response, error = await lrr_client.search_api.get_random_archives(GetRandomArchivesRequest(count=20))
    assert not error, f"Failed to get random archives (status {error.status}): {error.error}"
    assert len(response.data) == 20
    del response, error

    response, error = await lrr_client.search_api.get_random_archives(GetRandomArchivesRequest(count=20, newonly=True))
    assert not error, f"Failed to get random archives (status {error.status}): {error.error}"
    assert len(response.data) == 20
    del response, error

    response, error = await lrr_client.search_api.get_random_archives(GetRandomArchivesRequest(count=20, untaggedonly=True))
    assert not error, f"Failed to get random archives (status {error.status}): {error.error}"
    assert len(response.data) == 0
    del response, error
    # <<<<< SEARCH STAGE <<<<<

    # >>>>> DISCARD SEARCH CACHE STAGE >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"
    del response, error
    # <<<<< DISCARD SEARCH CACHE STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)
