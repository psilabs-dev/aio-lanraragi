"""
Collection of all simple API testing pipelines for the LANraragi server.

For each testing pipeline, a corresponding LRR environment is set up and torn down.

windows-2025 dev environments on Github are extremely flaky and prone to network problems.
We add a flake tank at the front, and rerun test cases in Windows on flake errors.
"""

import asyncio
import http
import logging
from pathlib import Path
import sys
import tempfile
import time
from typing import Dict, Generator, List, Tuple
import uuid
import numpy as np
import pytest
import pytest_asyncio
from urllib.parse import urlparse, parse_qs

from lanraragi.clients.client import LRRClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.archive import (
    DeleteArchiveRequest,
    DeleteArchiveResponse,
    ExtractArchiveRequest,
)
from lanraragi.models.base import (
    LanraragiErrorResponse,
    LanraragiResponse
)
from lanraragi.models.category import (
    AddArchiveToCategoryRequest,
    AddArchiveToCategoryResponse,
    GetCategoryRequest,
    GetCategoryResponse,
    RemoveArchiveFromCategoryRequest,
)

from aio_lanraragi_tests.helpers import expect_no_error_logs, save_archives, upload_archives
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext

LOGGER = logging.getLogger(__name__)
ENABLE_SYNC_FALLBACK = False # for debugging.

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
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)

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
    yield asyncio.BoundedSemaphore(value=8)

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

async def load_pages_from_archive(client: LRRClient, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[LanraragiResponse, LanraragiErrorResponse]:
    async with semaphore:
        # Use retry logic for extract_archive as it can encounter 423 errors
        response, error = await retry_on_lock(lambda: client.archive_api.extract_archive(ExtractArchiveRequest(arcid=arcid, force=False)))
        if error:
            return (None, error)
        
        pages = response.pages
        tasks = []
        async def load_page(page_api: str):
            url = client.build_url(page_api)
            url_parsed = urlparse(url)
            params = parse_qs(url_parsed.query)
            url = url.split("?")[0]
            try:
                status, content = await client.download_file(url, client.headers, params=params)
            except asyncio.TimeoutError:
                timeout_msg = f"Request timed out after {client.client_session.timeout.total}s"
                LOGGER.error(f"Failed to get page {page_api} (timeout): {timeout_msg}")
                return (None, _build_err_response(timeout_msg, 500))
            if status == 200:
                return (content, None)
            return (None, _build_err_response(content, status)) # TODO: this is wrong.
        for page in pages[:3]:
            tasks.append(asyncio.create_task(load_page(page)))
        gathered: List[Tuple[bytes, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for _, error in gathered:
            if error:
                return (None, error)
        return (LanraragiResponse(), None)

async def get_bookmark_category_detail(client: LRRClient, semaphore: asyncio.Semaphore) -> Tuple[GetCategoryResponse, LanraragiErrorResponse]:
    async with semaphore:
        response, error = await client.category_api.get_bookmark_link()
        assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
        category_id = response.category_id
        response, error = await client.category_api.get_category(GetCategoryRequest(category_id=category_id))
        assert not error, f"Failed to get category (status {error.status}): {error.error}"
        return (response, error)

async def delete_archive(client: LRRClient, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[DeleteArchiveResponse, LanraragiErrorResponse]:
    retry_count = 0
    async with semaphore:
        while True:
            response, error = await client.archive_api.delete_archive(DeleteArchiveRequest(arcid=arcid))
            if error and error.status == 423: # locked resource
                retry_count += 1
                if retry_count > 10:
                    return response, error
                tts = 2 ** retry_count
                LOGGER.debug(f"[delete_archive][{arcid}] locked resource error; retrying in {tts}s.")
                await asyncio.sleep(tts)
                continue
            return response, error

async def add_archive_to_category(client: LRRClient, category_id: str, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[AddArchiveToCategoryResponse, LanraragiErrorResponse]:
    retry_count = 0
    async with semaphore:
        while True:
            response, error = await client.category_api.add_archive_to_category(AddArchiveToCategoryRequest(category_id=category_id, arcid=arcid))
            if error and error.status == 423: # locked resource
                retry_count += 1
                if retry_count > 10:
                    return response, error
                tts = 2 ** retry_count
                LOGGER.debug(f"[add_archive_to_category][{category_id}][{arcid}] locked resource error; retrying in {tts}s.")
                await asyncio.sleep(tts)
                continue
            return response, error

async def remove_archive_from_category(client: LRRClient, category_id: str, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[LanraragiResponse, LanraragiErrorResponse]:
    retry_count = 0
    async with semaphore:
        while True:
            response, error = await client.category_api.remove_archive_from_category(RemoveArchiveFromCategoryRequest(category_id=category_id, arcid=arcid))
            if error and error.status == 423: # locked resource
                retry_count += 1
                if retry_count > 10:
                    return response, error
                tts = 2 ** retry_count
                LOGGER.debug(f"[remove_archive_from_category][{category_id}][{arcid}] locked resource error; retrying in {tts}s.")
                await asyncio.sleep(tts)
                continue
            return response, error

async def retry_on_lock(operation_func, max_retries: int = 10) -> Tuple[LanraragiResponse, LanraragiErrorResponse]:
    """
    Wrapper function that retries an operation if it encounters a 423 locked resource error.
    """
    retry_count = 0
    while True:
        response, error = await operation_func()
        if error and error.status == 423: # locked resource
            retry_count += 1
            if retry_count > max_retries:
                return response, error
            await asyncio.sleep(2 ** retry_count)
            continue
        return response, error

def pmf(t: float) -> float:
    return 2 ** (-t * 100)

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

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_logrotation(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext, semaphore: asyncio.BoundedSemaphore):
    """
    Pressure test LRR log rotation with custom endpoint.
    """
    batch_size = 1000
    start_time = time.time()
    num_batches = 50

    # Pre-create UUID batches to send and later verify in logs
    uuid_batches: List[List[str]] = [[str(uuid.uuid4()) for _ in range(batch_size)] for _ in range(num_batches)]
    all_uuids: List[str] = [u for batch in uuid_batches for u in batch]

    async def post_one(msg: str) -> Tuple[int, str]:
        async with semaphore:
            return await lrr_client.handle_request(
                http.HTTPMethod.POST,
                lrr_client.build_url('/api/logs/test'),
                lrr_client.headers,
                json_data=[msg]
            )

    for batch_idx, messages in enumerate(uuid_batches):
        tasks: List[asyncio.Task] = [asyncio.create_task(post_one(m)) for m in messages]
        results: List[Tuple[int, str]] = await asyncio.gather(*tasks)
        for status, content in results:
            assert status == 200, f"Logging API returned not OK: {content}"
        LOGGER.info(f"Completed batch: {batch_idx}")

    total_time = time.time() - start_time
    LOGGER.info(f"Completed test_logrotation with time {total_time}s.")

    # Verify all UUIDs were logged (including rotated logs)
    logs_text: str = environment.read_lrr_logs()
    not_found: List[str] = []
    for u in all_uuids:
        if u not in logs_text:
            not_found.append(u)

    # assert that no more than 0.1% of logs are not captured.
    # 50_000 * 0.001 = 50
    assert len(not_found) < 50, f"UUID not found in logs exceeds 50: {not_found}"

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_append_logrotation(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext, semaphore: asyncio.BoundedSemaphore):
    """
    Pressure test LRR log rotation with custom endpoint.
    Assert that the number of rotation files exceeds 2.
    """
    status, content = await lrr_client.handle_request(
        http.HTTPMethod.POST,
        lrr_client.build_url('/api/logs/test_append'),
        lrr_client.headers
    )
    assert status == 200, f"Append logging API returned not OK: {content}"

    # Give the logger a brief moment to finalize rotations
    await asyncio.sleep(1)

    rotated_logs = list(environment.logs_dir.glob("lanraragi.log.*.gz"))
    breakpoint()
    assert len(rotated_logs) > 2, f"Expected more than 2 rotated log files, found {len(rotated_logs)}"

    expect_no_error_logs(environment)
