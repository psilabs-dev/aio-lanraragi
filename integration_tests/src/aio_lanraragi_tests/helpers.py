import sys
import aiofiles
import asyncio
import errno
import logging
import numpy as np
from pathlib import Path
import tempfile
from typing import Awaitable, Callable, List, Optional, Set, Tuple, TypeVar
from urllib.parse import urlparse, parse_qs

import aiohttp

from lanraragi.clients.client import LRRClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.archive import (
    DeleteArchiveRequest,
    DeleteArchiveResponse,
    ExtractArchiveRequest,
    UploadArchiveRequest,
    UploadArchiveResponse,
)
from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse
from lanraragi.models.category import (
    AddArchiveToCategoryRequest,
    AddArchiveToCategoryResponse,
    GetCategoryRequest,
    GetCategoryResponse,
    RemoveArchiveFromCategoryRequest,
)

from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.metadata.zipf_utils import get_archive_idx_to_tag_idxs_map
from aio_lanraragi_tests.archive_generation.models import CreatePageRequest, WriteArchiveRequest, WriteArchiveResponse
from aio_lanraragi_tests.common import compute_upload_checksum
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.log_parse import parse_lrr_logs

LOGGER = logging.getLogger(__name__)
ResponseT = TypeVar('ResponseT', bound=LanraragiResponse)

def get_bounded_sem(on_unix: int=8, on_windows: int=2) -> asyncio.Semaphore:
    """
    Return a semaphore based on appropriate environment.
    """
    match sys.platform:
        case "win32":
            return asyncio.BoundedSemaphore(value=on_windows)
        case _:
            return asyncio.BoundedSemaphore(value=on_unix)

async def upload_archive(
    client: LRRClient, save_path: Path, filename: str, semaphore: asyncio.Semaphore,
    checksum: str=None, title: str=None, tags: str=None,
    max_retries: int=4, allow_duplicates: bool=False, retry_on_ise: bool=False,
    stop_event: Optional[asyncio.Event]=None,
) -> Tuple[UploadArchiveResponse, LanraragiErrorResponse]:
    """
    Upload archive (while considering all the permutations of errors that can happen).
    One can argue that this should be in the client library...

    Note: retry_on_ise SHOULDN'T be enabled otherwise it defeats the purpose of our tests.
    """

    # Considerations for github action integration testing
    # As uploads are performed in a timed environment on github actions, it's unnecessary to continue
    # uploading archives if the first archive uploads failed due to persistent connection refusal errors.
    # In this case, we should cancel on persistent error, then fail early, allowing the test to be
    # rerun, or the rest of the test to resume.
    if stop_event is not None and stop_event.is_set():
        raise asyncio.CancelledError()

    async with semaphore:

        # Check again bc most tasks will be queueing for semaphore use.
        if stop_event is not None and stop_event.is_set():
            raise asyncio.CancelledError()

        async with aiofiles.open(save_path, 'rb') as f:
            file = await f.read()
            request = UploadArchiveRequest(file=file, filename=filename, title=title, tags=tags, file_checksum=checksum)

        retry_count = 0
        while True:
            try:
                response, error = await client.archive_api.upload_archive(request)
                if error:
                    if error.status == 409:
                        if allow_duplicates:
                            LOGGER.info(f"[upload_archive] Duplicate upload {filename} to arcid {response.arcid}..")
                            return response, None
                        else:
                            LOGGER.error(f"[upload_archive] Duplicate upload {filename} to arcid {response.arcid}.")
                            return response, error
                    elif error.status == 423: # locked resource
                        if retry_count >= max_retries:
                            return None, error
                        tts = 2 ** retry_count
                        LOGGER.warning(f"[upload_archive] Locked resource when uploading {filename}. Retrying in {tts}s ({retry_count+1}/{max_retries})...")
                        await asyncio.sleep(tts)
                        retry_count += 1
                        continue
                    # retrying on internal server errors
                    elif error.status == 500 and retry_on_ise:
                        if retry_count >= max_retries:
                            return None, error
                        tts = 10
                        LOGGER.warning(f"[upload_archive] Encountered server error when uploading {filename} (message: {error.error}). Retrying in {tts}s ({retry_count+1}/{max_retries})...")
                        await asyncio.sleep(tts)
                        retry_count += 1
                        continue
                    else:
                        LOGGER.error(f"[upload_archive] Failed to upload {filename} (status: {error.status}): {error.error}")
                        return None, error

                LOGGER.debug(f"[upload_archive][{response.arcid}][{filename}]")
                return response, None
            except asyncio.TimeoutError as timeout_error:
                # if LRR handles files synchronously then our concurrent uploads may put too much pressure.
                # employ retry with exponential backoff here as well. This is not considered a server-side
                # problem.
                if retry_count >= max_retries:
                    error = LanraragiErrorResponse(error=str(timeout_error), status=408)
                    return None, error
                tts = 2 ** retry_count
                LOGGER.warning(f"[upload_archive] Encountered timeout exception while uploading {filename}, retrying in {tts}s ({retry_count+1}/{max_retries})...")
                await asyncio.sleep(tts)
                retry_count += 1
                continue
            except aiohttp.client_exceptions.ClientConnectorError as client_connector_error:
                # ClientConnectorError is a subclass of ClientOSError.
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
                    LOGGER.error(f"[upload_archive] Encountered error not related to connection while uploading {filename}: os_errno={os_errno}, os_winerr={os_winerr}")
                    raise client_connector_error

                if retry_count >= max_retries:
                    if stop_event is not None:
                        stop_event.set()
                        LOGGER.error("[upload_archive] Signalling STOP archive upload due to persistent connection errors.")
                    error = LanraragiErrorResponse(error=str(client_connector_error), status=408)
                    # return None, error
                    raise client_connector_error
                tts = 2 ** retry_count
                LOGGER.warning(
                    f"[upload_archive] Connection refused while uploading {filename}, retrying in {tts}s "
                    f"({retry_count+1}/{max_retries}); os_errno={os_errno}; os_winerr={os_winerr}"
                )
                await asyncio.sleep(tts)
                retry_count += 1
                continue
            except aiohttp.client_exceptions.ClientOSError as client_os_error:
                # this also happens sometimes.
                if retry_count >= max_retries:
                    error = LanraragiErrorResponse(error=str(client_os_error), status=408)
                    return None, error
                tts = 2 ** retry_count
                LOGGER.warning(f"[upload_archive] Encountered client OS error while uploading {filename}, retrying in {tts}s ({retry_count+1}/{max_retries})...")
                await asyncio.sleep(tts)
                retry_count += 1
                continue
            # just raise whatever else comes up because we should handle them explicitly anyways

def expect_no_error_logs(environment: AbstractLRRDeploymentContext):
    """
    Assert no logs with error level severity in LRR and Shinobu.
    """
    for event in parse_lrr_logs(environment.read_lrr_logs()):
        assert event.severity_level != 'error', "LANraragi process emitted error logs."
    
    if environment.shinobu_logs_path.exists():
        for event in parse_lrr_logs(environment.read_log(environment.shinobu_logs_path)):
            assert event.severity_level != 'error', "Shinobu process emitted error logs."
    else:
        LOGGER.warning("No shinobu logs found.")

async def upload_archives(
    write_responses: List[WriteArchiveResponse],
    npgenerator: np.random.Generator, semaphore: asyncio.Semaphore, lrr_client: LRRClient, force_sync: bool=False
) -> List[UploadArchiveResponse]:
    responses: List[UploadArchiveResponse] = []

    num_archives = len(write_responses)
    num_tags = 100
    arcidx_to_tagidx_list = get_archive_idx_to_tag_idxs_map(num_archives, num_tags, 1, 20, generator=npgenerator)
    stop_event = asyncio.Event()

    if force_sync:
        for i, _response in enumerate(write_responses):
            if stop_event.is_set():
                break
            title = f"Archive {i}"
            tag_idx_list = arcidx_to_tagidx_list[i]
            tag_list = [f"tag-{t}" for t in tag_idx_list]
            tags = ','.join(tag_list)
            checksum = compute_upload_checksum(_response.save_path)
            response, error = await upload_archive(
                lrr_client, _response.save_path, _response.save_path.name, semaphore,
                title=title, tags=tags, checksum=checksum, stop_event=stop_event
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            responses.append(response)
        return responses
    else: 
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tag_idx_list = arcidx_to_tagidx_list[i]
            tag_list = [f"tag-{t}" for t in tag_idx_list]
            tags = ','.join(tag_list)
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(
                    lrr_client, _response.save_path, _response.save_path.name, semaphore,
                    title=title, tags=tags, checksum=checksum, stop_event=stop_event
                )
            ))
        # Collect results; other tasks may be cancelled if a fatal connector error occurs.
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        # post-gather handling.
        # if any unexpected error or exception occurs: throw them.
        # if a client connection error occurred: throw it to trigger a flake rerun.
        first_connector_error: Optional[aiohttp.client_exceptions.ClientConnectorError] = None
        for item in gathered:
            if isinstance(item, tuple):
                response, error = item
                assert not error, f"Upload failed (status {error.status}): {error.error}"
                responses.append(response)
            elif isinstance(item, aiohttp.client_exceptions.ClientConnectorError):
                if first_connector_error is None:
                    first_connector_error = item
            elif isinstance(item, asyncio.CancelledError):
                if stop_event.is_set():
                    continue
                else:
                    raise item
            elif isinstance(item, BaseException):
                raise item
            else:
                raise RuntimeError(f"Unexpected gather result type: {type(item)}")
        if first_connector_error is not None:
            raise first_connector_error
        return responses

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
            # create_page_request = CreatePageRequest(1080, 1920, page_filename, image_format='PNG', text=page_text)
            create_page_request = CreatePageRequest(
                width=1080, height=1920, filename=page_filename, image_format='PNG', text=page_text
            )
            create_page_requests.append(create_page_request)
        requests.append(WriteArchiveRequest(create_page_requests=create_page_requests, save_path=save_path, archival_strategy=ArchivalStrategyEnum.ZIP))
    responses = write_archives_to_disk(requests)
    return responses


def create_archive_file(tmpdir: Path, name: str, num_pages: int) -> Path:
    """Create a single archive with the specified number of pages."""
    filename = f"{name}.zip"
    save_path = tmpdir / filename

    create_page_requests = []
    for page_id in range(num_pages):
        page_text = f"{name}-pg-{str(page_id + 1).zfill(len(str(num_pages)))}"
        page_filename = f"{page_text}.png"
        create_page_requests.append(CreatePageRequest(
            width=100, height=100, filename=page_filename, image_format='PNG', text=page_text
        ))

    request = WriteArchiveRequest(
        create_page_requests=create_page_requests,
        save_path=save_path,
        archival_strategy=ArchivalStrategyEnum.ZIP
    )
    responses = write_archives_to_disk([request])
    assert responses[0].save_path == save_path
    return save_path

async def retry_on_lock(
    operation_func: Callable[[], Awaitable[Tuple[ResponseT, LanraragiErrorResponse]]],
    max_retries: int = 10
) -> Tuple[ResponseT, LanraragiErrorResponse]:
    """
    Wrapper function that retries an operation if it encounters a 423 locked resource error.
    """
    retry_count = 0
    while True:
        response, error = await operation_func()
        if error and error.status == 423:
            retry_count += 1
            if retry_count > max_retries:
                return response, error
            await asyncio.sleep(2 ** retry_count)
            continue
        return response, error


async def delete_archive(client: LRRClient, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[DeleteArchiveResponse, LanraragiErrorResponse]:
    """Delete an archive with retry logic for locked resources."""
    retry_count = 0
    async with semaphore:
        while True:
            response, error = await client.archive_api.delete_archive(DeleteArchiveRequest(arcid=arcid))
            if error and error.status == 423:
                retry_count += 1
                if retry_count > 10:
                    return response, error
                tts = 2 ** retry_count
                LOGGER.debug(f"[delete_archive][{arcid}] locked resource error; retrying in {tts}s.")
                await asyncio.sleep(tts)
                continue
            return response, error


async def add_archive_to_category(client: LRRClient, category_id: str, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[AddArchiveToCategoryResponse, LanraragiErrorResponse]:
    """Add an archive to a category with retry logic for locked resources."""
    retry_count = 0
    async with semaphore:
        while True:
            response, error = await client.category_api.add_archive_to_category(AddArchiveToCategoryRequest(category_id=category_id, arcid=arcid))
            if error and error.status == 423:
                retry_count += 1
                if retry_count > 10:
                    return response, error
                tts = 2 ** retry_count
                LOGGER.debug(f"[add_archive_to_category][{category_id}][{arcid}] locked resource error; retrying in {tts}s.")
                await asyncio.sleep(tts)
                continue
            return response, error


async def remove_archive_from_category(client: LRRClient, category_id: str, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[LanraragiResponse, LanraragiErrorResponse]:
    """Remove an archive from a category with retry logic for locked resources."""
    retry_count = 0
    async with semaphore:
        while True:
            response, error = await client.category_api.remove_archive_from_category(RemoveArchiveFromCategoryRequest(category_id=category_id, arcid=arcid))
            if error and error.status == 423:
                retry_count += 1
                if retry_count > 10:
                    return response, error
                tts = 2 ** retry_count
                LOGGER.debug(f"[remove_archive_from_category][{category_id}][{arcid}] locked resource error; retrying in {tts}s.")
                await asyncio.sleep(tts)
                continue
            return response, error


async def load_pages_from_archive(client: LRRClient, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[LanraragiResponse, LanraragiErrorResponse]:
    """Load pages from an archive (extracts and fetches first 3 pages)."""
    async with semaphore:
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
            return (None, _build_err_response(content, status))
        for page in pages[:3]:
            tasks.append(asyncio.create_task(load_page(page)))
        gathered: List[Tuple[bytes, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for _, error in gathered:
            if error:
                return (None, error)
        return (LanraragiResponse(), None)


async def get_bookmark_category_detail(client: LRRClient, semaphore: asyncio.Semaphore) -> Tuple[GetCategoryResponse, LanraragiErrorResponse]:
    """Get the bookmark category details."""
    async with semaphore:
        response, error = await client.category_api.get_bookmark_link()
        assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
        category_id = response.category_id
        response, error = await client.category_api.get_category(GetCategoryRequest(category_id=category_id))
        assert not error, f"Failed to get category (status {error.status}): {error.error}"
        return (response, error)


async def xfail_catch_flakes_inner(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
    num_archives: int = 10,
    npgenerator: Optional[np.random.Generator] = None,
) -> None:
    """
    Inner implementation for xfail flake-catching test cases.

    THIS FUNCTION EXISTS SOLELY TO BE CALLED BY TEST CASES.

    On Windows test environments (particularly in CI), the first test case in a module
    that performs concurrent archive uploads often fails due to network flakes such as:
    - Remote host connection closures
    - Connection refused errors
    - High client request pressure overwhelming an unprepared host

    This function "warms up" the Windows test host by performing a lightweight archive
    upload operation before the actual test cases run. The calling test case should be
    decorated with:
        @pytest.mark.skipif(sys.platform != "win32", reason="Cache priming required only for flaky Windows testing environments.")
        @pytest.mark.asyncio
        @pytest.mark.xfail

    The xfail marker ensures that occasional failures in this warmup test are expected
    and ignored, while still providing the cache-priming benefit for subsequent tests.

    Args:
        lrr_client:     The LANraragi client instance.
        semaphore:      Semaphore for controlling concurrent operations.
        environment:    The LRR deployment context.
        num_archives:   Number of archives to upload for warmup (default: 10 for light warmup,
                        use 100 for heavy warmup in tests with bulk uploads).
        npgenerator:    Optional numpy random generator. If provided, uses save_archives() +
                        upload_archives() for random archive generation. If None, uses
                        create_archive_file() + upload_archive() for deterministic generation.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    LOGGER.debug("Established connection with test LRR server.")
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    assert not any(environment.archives_dir.iterdir()), "Archive directory is not empty!"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {num_archives} archives to upload for warmup.")

        if npgenerator is not None:
            # Heavy warmup: use random archive generation
            write_responses = save_archives(num_archives, tmpdir, npgenerator)
            assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"
            await upload_archives(write_responses, npgenerator, semaphore, lrr_client)
        else:
            # Light warmup: use deterministic archive generation
            archive_specs = [
                {"name": f"warmup_{i}", "title": f"Warmup Archive {i}", "tags": "warmup:test", "pages": 5}
                for i in range(num_archives)
            ]
            for spec in archive_specs:
                save_path = create_archive_file(tmpdir, spec["name"], spec["pages"])
                await upload_archive(
                    lrr_client, save_path, save_path.name, semaphore,
                    title=spec["title"], tags=spec["tags"]
                )
    # <<<<< UPLOAD STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)