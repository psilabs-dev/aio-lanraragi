"""
Search algorithm test cases.

Tests the following functionalities live:
- filter accuracy
- sorting accuracy
"""

import asyncio
import errno
import logging
from pathlib import Path
import sys
import tempfile
from typing import Generator, List, Optional, Set, Tuple
from aio_lanraragi_tests.deployment.factory import generate_deployment
import numpy as np
import pytest
import pytest_asyncio
import aiohttp
import aiohttp.client_exceptions

from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import (
    UploadArchiveRequest,
    UploadArchiveResponse,
)
from lanraragi.models.base import (
    LanraragiErrorResponse,
    LanraragiResponse
)

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import compute_upload_checksum
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.models import (
    CreatePageRequest,
    WriteArchiveRequest,
    WriteArchiveResponse,
)
from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.metadata import create_tag_generators, get_tag_assignments
from aio_lanraragi_tests.s1.utils import load_dataset
from aio_lanraragi_tests.s1.models import S1ArchiveInfo

LOGGER = logging.getLogger(__name__)

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture(autouse=True)
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    environment.setup(with_api_key=True, lrr_debug_mode=is_lrr_debug_mode)
    request.session.lrr_environment = environment
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
async def lanraragi(environment: AbstractLRRDeploymentContext) ->  Generator[LRRClient, None, None]:
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()

async def upload_archive(
    client: LRRClient, save_path: Path, filename: str, semaphore: asyncio.Semaphore, checksum: str=None, title: str=None, tags: str=None,
    max_retries: int=4
) -> Tuple[UploadArchiveResponse, LanraragiErrorResponse]:
    async with semaphore:
        with open(save_path, 'rb') as f:  # noqa: ASYNC230
            file = f.read()
            request = UploadArchiveRequest(file=file, filename=filename, title=title, tags=tags, file_checksum=checksum)

        retry_count = 0
        while True:
            try:
                response, error = await client.archive_api.upload_archive(request)
                if response:
                    return response, error
                if error.status == 423: # locked resource
                    if retry_count >= max_retries:
                        return None, error
                    tts = 2 ** retry_count
                    LOGGER.warning(f"Locked resource when uploading {filename}. Retrying in {tts}s ({retry_count+1}/{max_retries})...")
                    await asyncio.sleep(tts)
                    retry_count += 1
                    continue
            except asyncio.TimeoutError as timeout_error:
                # if LRR handles files synchronously then our concurrent uploads may put too much pressure.
                # employ retry with exponential backoff here as well. This is not considered a server-side
                # problem.
                if retry_count >= max_retries:
                    error = LanraragiErrorResponse(error=str(timeout_error), status=408)
                    return None, error
                tts = 2 ** retry_count
                LOGGER.warning(f"Encountered timeout exception while uploading {filename}, retrying in {tts}s ({retry_count+1}/{max_retries})...")
                await asyncio.sleep(tts)
                retry_count += 1
                continue
            except aiohttp.client_exceptions.ClientConnectorError as client_connector_error:
                inner_os_error: OSError = client_connector_error.os_error
                os_errno: Optional[int] = getattr(inner_os_error, "errno", None)
                os_winerr: Optional[int] = getattr(inner_os_error, "winerror", None)

                POSIX_REFUSED: Set[int] = {errno.ECONNREFUSED}
                if hasattr(errno, "WSAECONNREFUSED"):
                    POSIX_REFUSED.add(errno.WSAECONNREFUSED)
                if hasattr(errno, "WSAECONNRESET"):
                    POSIX_REFUSED.add(errno.WSAECONNRESET)

                # 64: The specified network name is no longer available
                # 1225: ERROR_CONNECTION_REFUSED
                # 10054: An existing connection was forcibly closed by the remote host
                # 10061: WSAECONNREFUSED
                WIN_REFUSED = {64, 1225, 10054, 10061}
                is_connection_refused = (
                    (os_winerr in WIN_REFUSED) or
                    (os_errno in POSIX_REFUSED) or
                    isinstance(inner_os_error, ConnectionRefusedError)
                )

                if not is_connection_refused:
                    LOGGER.error(f"Encountered error not related to connection while uploading {filename}: os_errno={os_errno}, os_winerr={os_winerr}")
                    raise client_connector_error

                if retry_count >= max_retries:
                    error = LanraragiErrorResponse(error=str(client_connector_error), status=408)
                    # return None, error
                    raise client_connector_error
                tts = 2 ** retry_count
                LOGGER.warning(
                    f"Connection refused while uploading {filename}, retrying in {tts}s "
                    f"({retry_count+1}/{max_retries}); os_errno={os_errno}; os_winerr={os_winerr}"
                )
                await asyncio.sleep(tts)
                retry_count += 1
                continue

            # just raise whatever else comes up because we should handle them explicitly anyways

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

def save_archives(num_archives: int, work_dir: Path, np_generator: np.random.Generator) -> List[WriteArchiveResponse]:
    requests = []
    responses = []
    for archive_id in range(num_archives):
        create_page_requests = []
        archive_name = f"archive-{str(archive_id+1).zfill(len(str(num_archives)))}"
        filename = f"{archive_name}.zip"
        save_path = work_dir / filename
        num_pages = np_generator.integers(10, 20)
        for page_id in range(num_pages):
            page_text = f"{archive_name}-pg-{str(page_id+1).zfill(len(str(num_pages)))}"
            page_filename = f"{page_text}.png"
            create_page_request = CreatePageRequest(1080, 1920, page_filename, image_format='PNG', text=page_text)
            create_page_requests.append(create_page_request)        
        requests.append(WriteArchiveRequest(create_page_requests, save_path, ArchivalStrategyEnum.ZIP))
    responses = write_archives_to_disk(requests)
    return responses

async def populate_server(
    lanraragi: LRRClient,
    semaphore: asyncio.Semaphore,
    s1_archives: List[S1ArchiveInfo]
) -> List[str]:
    """
    Create archive files for the given S1 dataset archives and upload them to the server.

    Returns list of uploaded arcids.
    """
    arcids: List[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        requests: List[WriteArchiveRequest] = []
        for a in s1_archives:
            create_page_requests: List[CreatePageRequest] = []
            num_pages = max(1, int(a.pages))
            for page_id in range(num_pages):
                page_text = f"s1-{a.preid}-pg-{str(page_id+1).zfill(len(str(num_pages)))}"
                page_filename = f"{page_text}.png"
                create_page_requests.append(
                    CreatePageRequest(1080, 1920, page_filename, image_format='PNG', text=page_text)
                )

            save_path = tmpdir_path / f"s1-{a.preid}.zip"
            requests.append(WriteArchiveRequest(create_page_requests, save_path, ArchivalStrategyEnum.ZIP))

        write_responses = write_archives_to_disk(requests)

        tasks = []
        for a, wr in zip(s1_archives, write_responses):
            checksum = compute_upload_checksum(wr.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(
                    lanraragi,
                    wr.save_path,
                    wr.save_path.name,
                    semaphore,
                    title=a.title,
                    tags=a.tags,
                    checksum=checksum,
                )
            ))

        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            arcids.append(response.arcid)

    return arcids

# We'll still need this since we're uploading a bunch of archives.
@pytest.mark.skipif(sys.platform != "win32", reason="Cache priming required only for flaky Windows testing environments.")
@pytest.mark.asyncio
@pytest.mark.xfail
async def test_xfail_catch_flakes(lanraragi: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator):
    """
    This xfail test case serves no integration testing purpose, other than to prime the cache of flaky testing hosts
    and reduce the chances of subsequent test case failures caused by network flakes, such as remote host connection
    closures or connection refused errors resulting from high client request pressure to unprepared host.

    Therefore, occasional test case failures here are expected and ignored.
    """
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    LOGGER.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    tag_generators = create_tag_generators(100, pmf)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, npgenerator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        LOGGER.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, npgenerator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

@pytest.mark.asyncio
async def test_s1_exact_search(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
    """
    Search with filter "wmq6 Tkx9NI YXΩ Oy4cq K3αyn9ff Kxkdpi6q Ngac72qf", and get exactly one archive.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    LOGGER.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> LOAD S1 DATASET & UPLOAD ALL ARCHIVES >>>>>
    dataset = load_dataset()
    archives: List[S1ArchiveInfo] = dataset["archives"]
    await populate_server(lanraragi, semaphore, archives)
    # <<<<< LOAD S1 DATASET & UPLOAD ALL ARCHIVES <<<<<

    # >>>>> SEARCH STAGE >>>>>
    from lanraragi.models.search import SearchArchiveIndexRequest
    filter_str = "wmq6 Tkx9NI YXΩ Oy4cq K3αyn9ff Kxkdpi6q Ngac72qf"
    search_resp, search_err = await lanraragi.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter=filter_str)
    )
    assert not search_err, f"Failed to search archive index (status {search_err.status}): {search_err.error}"
    assert len(search_resp.data) == 1, f"Expected exactly 1 match, got {len(search_resp.data)}"
    # <<<<< SEARCH STAGE <<<<<