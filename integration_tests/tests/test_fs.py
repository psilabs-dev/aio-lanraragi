"""
Filesystem-related integration tests.
"""

import asyncio
import logging
import tempfile
import aiohttp
import numpy as np
from pathlib import Path
import shutil
import sys
from typing import Dict, Generator, List, Tuple
import pytest
import pytest_asyncio

from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import DeleteArchiveRequest, DeleteArchiveResponse
from lanraragi.models.base import LanraragiErrorResponse

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.helpers import expect_no_error_logs, get_bounded_sem, save_archives, upload_archives

LOGGER = logging.getLogger(__name__)
ENABLE_SYNC_FALLBACK = False

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture
def is_lrr_debug_mode(request: pytest.FixtureRequest) -> Generator[bool, None, None]:
    yield request.config.getoption("--lrr-debug")

@pytest.fixture
def environment(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int) -> Generator[AbstractLRRDeploymentContext, None, None]:
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    request.session.lrr_environment = environment

    # configure environments to session
    environments: Dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

    yield environment
    environment.teardown(remove_data=True)

@pytest.fixture
def symlink_archive_dir(environment: AbstractLRRDeploymentContext) -> Generator[Path, None, None]:
    """
    Creates symlink for archives dir (which should not be created yet)
    """
    archives_dir = environment.archives_dir
    symlink_dir = environment.staging_dir / (archives_dir.name + "_sym") # e.g. "/archives_sym"
    symlink_dir.mkdir(parents=True, exist_ok=True)
    archives_dir.symlink_to(symlink_dir, target_is_directory=True)
    yield symlink_dir

    try:
        archives_dir.unlink()
    except Exception as e:
        LOGGER.error(f"Unhandled exception when removing archives directory {archives_dir}: ", e)

    try:
        shutil.rmtree(symlink_dir)
    except Exception as e:
        LOGGER.error(f"Unhandled exception when removing symlink directory {symlink_dir}: ", e)

@pytest.fixture
def npgenerator(request: pytest.FixtureRequest) -> Generator[np.random.Generator, None, None]:
    seed: int = int(request.config.getoption("npseed"))
    generator = np.random.default_rng(seed)
    yield generator

@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield get_bounded_sem()

@pytest_asyncio.fixture
async def client_session() -> Generator[aiohttp.ClientSession, None, None]:
    session = aiohttp.ClientSession()
    yield session
    await session.close()

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
async def test_archive_upload_to_symlinked_dir(
    symlink_archive_dir: Path, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, is_lrr_debug_mode: bool,
    environment: AbstractLRRDeploymentContext, client_session: aiohttp.ClientSession
):
    """
    Tests archive uploads into a symlink directory. Similar to test_simple.py::test_archive_upload
    """
    assert environment.archives_dir.is_symlink(), "Archives directory is not symbolic link!"
    num_archives = 100

    # start up server.
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)
    lrr_client = environment.lrr_client(client_session=client_session)

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    LOGGER.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    assert not any(symlink_archive_dir.iterdir()), "Archive directory is not empty!"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, npgenerator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        LOGGER.debug("Uploading archives to server.")
        await upload_archives(write_responses, npgenerator, semaphore, lrr_client)
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> VALIDATE UPLOAD COUNT STAGE >>>>>
    LOGGER.debug("Validating upload counts.")
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"

    # get this data for archive deletion.
    arcs_delete_sync = response.data[:5]
    arcs_delete_async = response.data[5:50]
    assert len(response.data) == num_archives, "Number of archives on server does not equal number uploaded!"

    # validate number of archives actually in the file system.
    assert len(list(symlink_archive_dir.iterdir())) == num_archives, "Number of archives on disk does not equal number uploaded!"
    # <<<<< VALIDATE UPLOAD COUNT STAGE <<<<<

    # >>>>> GET DATABASE BACKUP STAGE >>>>>
    response, error = await lrr_client.database_api.get_database_backup()
    assert not error, f"Failed to get database backup (status {error.status}): {error.error}"
    assert len(response.archives) == num_archives, "Number of archives in database backup does not equal number uploaded!"
    del response, error
    # <<<<< GET DATABASE BACKUP STAGE <<<<<

    # >>>>> DELETE ARCHIVE SYNC STAGE >>>>>
    for archive in arcs_delete_sync:
        response, error = await lrr_client.archive_api.delete_archive(DeleteArchiveRequest(arcid=archive.arcid))
        assert not error, f"Failed to delete archive {archive.arcid} with status {error.status} and error: {error.error}"
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
    assert len(response.data) == num_archives-5, "Incorrect number of archives in server!"
    assert len(list(symlink_archive_dir.iterdir())) == num_archives-5, "Incorrect number of archives on disk!"
    # <<<<< DELETE ARCHIVE SYNC STAGE <<<<<

    # >>>>> DELETE ARCHIVE ASYNC STAGE >>>>>
    tasks = []
    for archive in arcs_delete_async:
        tasks.append(asyncio.create_task(delete_archive(lrr_client, archive.arcid, semaphore)))
    gathered: List[Tuple[DeleteArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
    for response, error in gathered:
        assert not error, f"Delete archive failed (status {error.status}): {error.error}"
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
    assert len(response.data) == num_archives-50, "Incorrect number of archives in server!"
    assert len(list(symlink_archive_dir.iterdir())) == num_archives-50, "Incorrect number of archives on disk!"
    # <<<<< DELETE ARCHIVE ASYNC STAGE <<<<<

    # # no error logs
    # # TODO: there are error logs (tankoubon missing archive ID)
    # expect_no_error_logs(environment)