import asyncio
import errno
import logging
from pathlib import Path
from typing import List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

import aiohttp

from lanraragi.clients.utils import _build_err_response
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import DeleteArchiveRequest, DeleteArchiveResponse, ExtractArchiveRequest, UploadArchiveRequest, UploadArchiveResponse
from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse
from lanraragi.models.category import AddArchiveToCategoryRequest, AddArchiveToCategoryResponse, GetCategoryRequest, GetCategoryResponse, RemoveArchiveFromCategoryRequest

LOGGER = logging.getLogger(__name__)

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

async def delete_archive(client: LRRClient, arcid: str, semaphore: asyncio.Semaphore) -> Tuple[DeleteArchiveResponse, LanraragiErrorResponse]:
    retry_count = 0
    async with semaphore:
        while True:
            response, error = await client.archive_api.delete_archive(DeleteArchiveRequest(arcid=arcid))
            if error and error.status == 423: # locked resource
                retry_count += 1
                if retry_count > 10:
                    return response, error
                await asyncio.sleep(2 ** retry_count)
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
                await asyncio.sleep(2 ** retry_count)
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
                await asyncio.sleep(2 ** retry_count)
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