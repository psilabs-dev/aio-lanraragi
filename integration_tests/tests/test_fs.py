"""
Filesystem-related integration tests.
"""

import asyncio
import logging
import shutil
import sys
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import aiohttp
import numpy as np
import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import (
    DeleteArchiveRequest,
    DeleteArchiveResponse,
    GetArchiveMetadataRequest,
)
from lanraragi.models.base import LanraragiErrorResponse

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    delete_archive,
    save_archives,
    upload_archive,
    upload_archives,
)
from aio_lanraragi_tests.utils.concurrency import get_bounded_sem
from aio_lanraragi_tests.utils.flakes import xfail_catch_flakes_inner

LOGGER = logging.getLogger(__name__)
ENABLE_SYNC_FALLBACK = False

@pytest.fixture
def resource_prefix(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    yield request.config.getoption("--resource-prefix") + "test_"

@pytest.fixture
def port_offset(request: pytest.FixtureRequest) -> Generator[int, None, None]:
    yield request.config.getoption("--port-offset") + 10

@pytest.fixture
def is_lrr_debug_mode(request: pytest.FixtureRequest) -> Generator[bool, None, None]:
    yield request.config.getoption("--lrr-debug")

@pytest.fixture
def environment(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int) -> Generator[AbstractLRRDeploymentContext, None, None]:
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)

    # configure environments to session
    environments: dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

    try:
        yield environment
    finally:
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
    except OSError as e:
        LOGGER.error(f"Unhandled exception when removing archives directory {archives_dir}: ", e)

    try:
        shutil.rmtree(symlink_dir)
    except OSError as e:
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
async def client_session() -> AsyncGenerator[aiohttp.ClientSession, None]:
    session = aiohttp.ClientSession()
    yield session
    await session.close()

@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
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
    await xfail_catch_flakes_inner(lrr_client, semaphore, environment, num_archives=100, npgenerator=npgenerator)

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
    gathered: list[tuple[DeleteArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
    for response, error in gathered:
        assert not error, f"Delete archive failed (status {error.status}): {error.error}"
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
    assert len(response.data) == num_archives-50, "Incorrect number of archives in server!"
    assert len(list(symlink_archive_dir.iterdir())) == num_archives-50, "Incorrect number of archives on disk!"
    # <<<<< DELETE ARCHIVE ASYNC STAGE <<<<<

    # # no error logs
    # TODO: reinstate this assertion once we've decided what to do with shinobu's file handling problem.
    # expect_no_error_logs(environment, LOGGER)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
@pytest.mark.xfail(reason="requires LRR-side fix (PR #1600): replacing a same-named archive with different content erases the new metadata", strict=False)
async def test_archive_replace(
    semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, is_lrr_debug_mode: bool,
    environment: AbstractLRRDeploymentContext
):
    """
    Replacing an existing archive with different content under the same filename keeps the new metadata.

    1. Start the server with replace-duplicate mode enabled.
    2. Upload content A as "collision.zip" tagged "replacetest:first".
    3. Restart Shinobu so its boot scan records collision.zip in the filemap.
    4. Upload different content as "collision.zip" tagged "replacetest:second".
    5. Wait for Shinobu to reconcile, then assert one archive remains holding the second tag, not the first.
    """
    filename = "collision.zip"

    # start up server with replace-duplicate enabled.
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)
    environment.enable_replace_dupe()
    lrr_client = environment.lrr_client()

    try:
        # >>>>> TEST CONNECTION STAGE >>>>>
        response, error = await lrr_client.misc_api.get_server_info()
        assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
        response, error = await lrr_client.archive_api.get_all_archives()
        assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
        assert len(response.data) == 0, "Server contains archives!"
        del response, error
        # <<<<< TEST CONNECTION STAGE <<<<<

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            first_path = create_archive_file(tmpdir, "content-first", 12)
            second_path = create_archive_file(tmpdir, "content-second", 13)

            # >>>>> FIRST UPLOAD STAGE >>>>>
            response, error = await upload_archive(lrr_client, first_path, filename, semaphore, tags="replacetest:first")
            assert not error, f"Failed to upload first archive (status {error.status}): {error.error}"
            del response, error
            # <<<<< FIRST UPLOAD STAGE <<<<<

            # >>>>> FILEMAP PRIMING STAGE >>>>>
            # Restart Shinobu so its boot scan records collision.zip in the filemap before the replacement upload;
            # the fix relies on that filemap entry to identify the archive being replaced.
            response, error = await lrr_client.shinobu_api.restart_shinobu()
            assert not error, f"Failed to restart shinobu (status {error.status}): {error.error}"
            retry_count = 0
            while retry_count < 10:
                response, error = await lrr_client.shinobu_api.get_shinobu_status()
                assert not error, f"Failed to get shinobu status (status {error.status}): {error.error}"
                if response.is_alive:
                    break
                retry_count += 1
                await asyncio.sleep(1)
            assert response.is_alive, "Shinobu did not come back alive after restart!"
            del response, error
            # Shinobu waits up to 5s for a sub-512KB archive to be "fully written" before recording it,
            # so give the boot scan margin beyond that floor to populate the filemap before replacing.
            await asyncio.sleep(12)
            # <<<<< FILEMAP PRIMING STAGE <<<<<

            # >>>>> REPLACEMENT UPLOAD STAGE >>>>>
            response, error = await upload_archive(lrr_client, second_path, filename, semaphore, tags="replacetest:second")
            assert not error, f"Failed to upload replacement archive (status {error.status}): {error.error}"
            second_arcid = response.arcid
            del response, error
            # <<<<< REPLACEMENT UPLOAD STAGE <<<<<

        # >>>>> RECONCILE STAGE >>>>>
        # Shinobu reacts to the replacement asynchronously; wait for it to converge to a single archive.
        retry_count = 0
        while retry_count < 30:
            response, error = await lrr_client.archive_api.get_all_archives()
            assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
            if len(response.data) == 1:
                break
            retry_count += 1
            await asyncio.sleep(1)
        assert len(response.data) == 1, f"Expected exactly one archive after replacement, found {len(response.data)}!"
        del response, error
        # <<<<< RECONCILE STAGE <<<<<

        # >>>>> VERIFY METADATA STAGE >>>>>
        response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=second_arcid))
        assert not error, f"Failed to get archive metadata (status {error.status}): {error.error}"
        tags = {tag.strip() for tag in response.tags.split(",")}
        assert "replacetest:second" in tags, f"New metadata was lost after replacement; tags are '{response.tags}'"
        assert "replacetest:first" not in tags, f"Old metadata resurfaced after replacement; tags are '{response.tags}'"
        del response, error
        # <<<<< VERIFY METADATA STAGE <<<<<
    finally:
        await lrr_client.close()
