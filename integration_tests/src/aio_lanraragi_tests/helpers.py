from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.metadata.zipf_utils import get_archive_idx_to_tag_idxs_map
from aio_lanraragi_tests.archive_generation.models import CreatePageRequest, WriteArchiveRequest, WriteArchiveResponse
from aio_lanraragi_tests.common import compute_upload_checksum
import aiofiles
import asyncio
import errno
import logging
import numpy as np
from pathlib import Path
from typing import List, Optional, Set, Tuple

import aiohttp

from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import UploadArchiveRequest, UploadArchiveResponse
from lanraragi.models.base import LanraragiErrorResponse

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.log_parse import parse_lrr_logs

LOGGER = logging.getLogger(__name__)

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
    for event in parse_lrr_logs(environment.read_log(environment.lanraragi_logs_path)):
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