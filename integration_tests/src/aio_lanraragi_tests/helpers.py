import aiofiles
import asyncio
import errno
import logging
from pathlib import Path
from typing import Optional, Set, Tuple

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
    max_retries: int=4
) -> Tuple[UploadArchiveResponse, LanraragiErrorResponse]:
    """
    Upload archive (while considering all the permutations of errors that can happen).
    One can argue that this should be in the client library...
    """

    async with semaphore:
        async with aiofiles.open(save_path, 'rb') as f:
            file = await f.read()
            request = UploadArchiveRequest(file=file, filename=filename, title=title, tags=tags, file_checksum=checksum)

        retry_count = 0
        while True:
            try:
                response, error = await client.archive_api.upload_archive(request)
                if error:
                    if error.status == 423: # locked resource
                        if retry_count >= max_retries:
                            return None, error
                        tts = 2 ** retry_count
                        LOGGER.warning(f"[upload_archive] Locked resource when uploading {filename}. Retrying in {tts}s ({retry_count+1}/{max_retries})...")
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