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
from typing import Dict, Generator, List, Tuple
import numpy as np
import pytest
import playwright.async_api
import pytest_asyncio
import aiohttp

from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import (
    ClearNewArchiveFlagRequest,
    DeleteArchiveRequest,
    DeleteArchiveResponse,
    GetArchiveCategoriesRequest,
    GetArchiveMetadataRequest,
    GetArchiveThumbnailRequest,
    UpdateArchiveThumbnailRequest,
    UpdateReadingProgressionRequest,
)
from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse
from lanraragi.models.category import (
    AddArchiveToCategoryResponse,
    CreateCategoryRequest,
    DeleteCategoryRequest,
    GetCategoryRequest,
    UpdateBookmarkLinkRequest,
    UpdateCategoryRequest,
)
from lanraragi.models.database import GetDatabaseStatsRequest
from lanraragi.models.minion import (
    GetMinionJobDetailRequest,
    GetMinionJobStatusRequest,
)
from lanraragi.models.misc import (
    GetAvailablePluginsRequest,
    GetOpdsCatalogRequest,
    RegenerateThumbnailRequest,
)
from lanraragi.models.tankoubon import (
    AddArchiveToTankoubonRequest,
    CreateTankoubonRequest,
    DeleteTankoubonRequest,
    GetTankoubonRequest,
    RemoveArchiveFromTankoubonRequest,
    TankoubonMetadata,
    UpdateTankoubonRequest,
)

from aio_lanraragi_tests.helpers import (
    add_archive_to_category,
    delete_archive,
    expect_no_error_logs,
    get_bookmark_category_detail,
    get_bounded_sem,
    load_pages_from_archive,
    remove_archive_from_category,
    retry_on_lock,
    save_archives,
    upload_archive,
    upload_archives,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import LRR_INDEX_TITLE

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

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_archive_upload(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    Creates 100 archives to upload to the LRR server,
    and verifies that this number of archives is correct.

    Then deletes 50 archives (5 sequentially, followed by
    45 concurrently). Verifies archive count is correct.
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

    # >>>>> VALIDATE UPLOAD COUNT STAGE >>>>>
    LOGGER.debug("Validating upload counts.")
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"

    # get this data for archive deletion.
    arcs_delete_sync = response.data[:5]
    arcs_delete_async = response.data[5:50]
    assert len(response.data) == num_archives, "Number of archives on server does not equal number uploaded!"

    # validate number of archives actually in the file system.
    assert len(list(environment.archives_dir.iterdir())) == num_archives, "Number of archives on disk does not equal number uploaded!"
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
    assert len(list(environment.archives_dir.iterdir())) == num_archives-5, "Incorrect number of archives on disk!"
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
    assert len(list(environment.archives_dir.iterdir())) == num_archives-50, "Incorrect number of archives on disk!"
    # <<<<< DELETE ARCHIVE ASYNC STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_archive_read(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    Simulates a read archive operation.
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

    # >>>>> GET ALL ARCHIVES STAGE >>>>>
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == num_archives, "Number of archives on server does not equal number uploaded!"
    first_archive_id = response.data[0].arcid

    # >>>>> TEST THUMBNAIL STAGE >>>>>
    response, error = await lrr_client.archive_api.get_archive_thumbnail(GetArchiveThumbnailRequest(arcid=first_archive_id, nofallback=True))
    assert not error, f"Failed to get thumbnail with no_fallback=True (status {error.status}): {error.error}"
    del response, error

    response, error = await lrr_client.archive_api.get_archive_thumbnail(GetArchiveThumbnailRequest(arcid=first_archive_id))
    assert not error, f"Failed to get thumbnail with default settings (status {error.status}): {error.error}"
    assert response.content is not None, "Thumbnail content should not be None with default settings"
    assert response.content_type is not None, "Expected content_type to be set in regular response"
    del response, error
    # <<<<< TEST THUMBNAIL STAGE <<<<<

    # >>>>> UPDATE ARCHIVE THUMBNAIL STAGE >>>>>
    response, error = await retry_on_lock(lambda: lrr_client.archive_api.update_thumbnail(UpdateArchiveThumbnailRequest(arcid=first_archive_id, page=2)))
    assert not error, f"Failed to update thumbnail to page 2 (status {error.status}): {error.error}"
    assert response.new_thumbnail, "Expected new_thumbnail field to be populated"
    del response, error
    # <<<<< UPDATE ARCHIVE THUMBNAIL STAGE <<<<<

    # <<<<< GET ALL ARCHIVES STAGE <<<<<

    # >>>>> SIMULATE READ ARCHIVE STAGE >>>>>
    # make these api calls concurrently:
    # DELETE /api/archives/:arcid/isnew
    # GET /api/archives/:arcid/metadata
    # GET /api/categories/bookmark_link
    # GET /api/categories/:category_id (bookmark category)
    # GET /api/archives/:arcid/files?force=false
    # PUT /api/archives/:arcid/progress/1
    # POST /api/archives/:arcid/files/thumbnails
    # GET /api/archives/:arcid/page?path=p_01.png (first three pages)

    tasks = []
    tasks.append(asyncio.create_task(retry_on_lock(lambda: lrr_client.archive_api.clear_new_archive_flag(ClearNewArchiveFlagRequest(arcid=first_archive_id)))))
    tasks.append(asyncio.create_task(retry_on_lock(lambda: lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=first_archive_id)))))
    tasks.append(asyncio.create_task(get_bookmark_category_detail(lrr_client, semaphore)))
    tasks.append(asyncio.create_task(load_pages_from_archive(lrr_client, first_archive_id, semaphore)))
    tasks.append(asyncio.create_task(retry_on_lock(lambda: lrr_client.archive_api.update_reading_progression(UpdateReadingProgressionRequest(arcid=first_archive_id, page=1)))))
    tasks.append(asyncio.create_task(retry_on_lock(lambda: lrr_client.archive_api.get_archive_thumbnail(GetArchiveThumbnailRequest(arcid=first_archive_id)))))

    results: List[Tuple[LanraragiResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
    for response, error in results:
        assert not error, f"Failed to complete task (status {error.status}): {error.error}"
    # <<<<< SIMULATE READ ARCHIVE STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_category(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Runs sanity tests against the category and bookmark link API.

    TODO: a more comprehensive test should be designed to verify that the first-time installation
    does not apply when a server is restarted. This should preferably be in a separate test module
    that is more involved with the server environment.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    response, error = await lrr_client.category_api.get_all_categories()
    assert not error, f"Failed to get all categories (status {error.status}): {error.error}"
    assert len(response.data) == 1, "Server does not contain exactly the bookmark category!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> GET BOOKMARK LINK >>>>>
    response, error = await lrr_client.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    category_id = response.category_id
    response, error = await lrr_client.category_api.get_category(GetCategoryRequest(category_id=category_id))
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    category_name = response.name
    assert category_name == '🔖 Favorites', "Bookmark is not linked to Favorites!"
    del response, error
    # <<<<< GET BOOKMARK LINK <<<<<

    # >>>>> CREATE CATEGORY >>>>>
    request = CreateCategoryRequest(name="test-static-category")
    response, error = await lrr_client.category_api.create_category(request)
    assert not error, f"Failed to create static category (status {error.status}): {error.error}"
    static_cat_id = response.category_id
    request = CreateCategoryRequest(name="test-dynamic-category", search="language:english")
    response, error = await lrr_client.category_api.create_category(request)
    assert not error, f"Failed to create dynamic category (status {error.status}): {error.error}"
    dynamic_cat_id = response.category_id
    del request, response, error
    # <<<<< CREATE CATEGORY <<<<<

    # >>>>> UPDATE CATEGORY >>>>>
    request = UpdateCategoryRequest(category_id=static_cat_id, name="test-static-category-changed")
    response, error = await lrr_client.category_api.update_category(request)
    assert not error, f"Failed to update category (status {error.status}): {error.error}"
    request = GetCategoryRequest(category_id=static_cat_id)
    response, error = await lrr_client.category_api.get_category(request)
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    assert response.name == "test-static-category-changed", "Category name is incorrect after update!"
    del request, response, error
    # <<<<< UPDATE CATEGORY <<<<<

    # >>>>> UPDATE BOOKMARK LINK >>>>>
    request = UpdateBookmarkLinkRequest(category_id=static_cat_id)
    response, error = await lrr_client.category_api.update_bookmark_link(request)
    assert not error, f"Failed to update bookmark link (status {error.status}): {error.error}"
    request = UpdateBookmarkLinkRequest(category_id=dynamic_cat_id)
    response, error = await lrr_client.category_api.update_bookmark_link(request)
    assert error and error.status == 400, "Assigning bookmark link to dynamic category should not be possible!"
    response, error = await lrr_client.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    # <<<<< UPDATE BOOKMARK LINK <<<<<

    # >>>>> DELETE BOOKMARK LINK >>>>>
    response, error = await lrr_client.category_api.disable_bookmark_feature()
    assert not error, f"Failed to disable bookmark link (status {error.status}): {error.error}"
    response, error = await lrr_client.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    assert not response.category_id, "Bookmark link should be empty after disabling!"
    # <<<<< DELETE BOOKMARK LINK <<<<<

    # >>>>> UNLINK BOOKMARK >>>>>
    request = CreateCategoryRequest(name="test-static-category-2")
    response, error = await lrr_client.category_api.create_category(request)
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    static_cat_id_2 = response.category_id
    del request, response, error
    request = UpdateBookmarkLinkRequest(category_id=static_cat_id_2)
    response, error = await lrr_client.category_api.update_bookmark_link(request)
    assert not error, f"Failed to update bookmark link (status {error.status}): {error.error}"
    # Delete the category that is linked to the bookmark
    request = DeleteCategoryRequest(category_id=static_cat_id_2)
    response, error = await lrr_client.category_api.delete_category(request)
    assert not error, f"Failed to delete category (status {error.status}): {error.error}"
    del request, response, error
    response, error = await lrr_client.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    assert not response.category_id, "Deleting a category linked to bookmark should unlink bookmark!"
    del response, error
    # <<<<< UNLINK BOOKMARK <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_archive_category_interaction(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    Creates 100 archives to upload to the LRR server, with an emphasis on testing category/archive addition/removal
    and asynchronous operations.

    1. upload 100 archives
    2. get bookmark link
    3. add 50 archives to bookmark
    4. check that 50 archives are in the bookmark category
    5. remove 50 archives from bookmark
    6. check that 0 archives are in the bookmark category
    7. add 50 archives to bookmark asynchronously
    8. check that 50 archives are in the bookmark category
    9. check archive category membership
    10. remove 50 archives from bookmark asynchronously
    11. check that 0 archives are in the bookmark category
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
    archive_ids = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, npgenerator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        LOGGER.debug("Uploading archives to server.")
        upload_responses = await upload_archives(write_responses, npgenerator, semaphore, lrr_client, force_sync=ENABLE_SYNC_FALLBACK)
        archive_ids = [response.arcid for response in upload_responses]
        del upload_responses
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> GET BOOKMARK LINK STAGE >>>>>
    response, error = await lrr_client.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    bookmark_cat_id = response.category_id
    del response, error
    # <<<<< GET BOOKMARK LINK STAGE <<<<<

    # >>>>> ADD ARCHIVE TO CATEGORY SYNC STAGE >>>>>
    for arcid in archive_ids[50:]:
        response, error = await add_archive_to_category(lrr_client, bookmark_cat_id, arcid, semaphore)
        assert not error, f"Failed to add archive to category (status {error.status}): {error.error}"
        del response, error
    # <<<<< ADD ARCHIVE TO CATEGORY SYNC STAGE <<<<<

    # >>>>> GET CATEGORY SYNC STAGE >>>>>
    response, error = await lrr_client.category_api.get_category(GetCategoryRequest(category_id=bookmark_cat_id))
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    assert len(response.archives) == 50, "Number of archives in bookmark category does not equal 50!"
    assert set(response.archives) == set(archive_ids[50:]), "Archives in bookmark category do not match!"
    del response, error
    # <<<<< GET CATEGORY SYNC STAGE <<<<<

    # >>>>> VERIFY ARCHIVE CATEGORY MEMBERSHIP VIA ARCHIVE API (SYNC) >>>>>
    # One included archive should report the bookmark category; one excluded should not.
    resp_in, err_in = await lrr_client.archive_api.get_archive_categories(GetArchiveCategoriesRequest(arcid=archive_ids[55]))
    assert not err_in, f"Failed to get archive categories (status {err_in.status}): {err_in.error}"
    cat_ids_in = {c.category_id for c in resp_in.categories}
    assert bookmark_cat_id in cat_ids_in, "Archive expected in bookmark category but was not reported by /archives/:id/categories"
    del resp_in, err_in

    resp_out, err_out = await lrr_client.archive_api.get_archive_categories(GetArchiveCategoriesRequest(arcid=archive_ids[10]))
    assert not err_out, f"Failed to get archive categories (status {err_out.status}): {err_out.error}"
    cat_ids_out = {c.category_id for c in resp_out.categories}
    assert bookmark_cat_id not in cat_ids_out, "Archive not in bookmark category was incorrectly reported by /archives/:id/categories"
    del resp_out, err_out
    # <<<<< VERIFY ARCHIVE CATEGORY MEMBERSHIP VIA ARCHIVE API (SYNC) <<<<<

    # >>>>> REMOVE ARCHIVE FROM CATEGORY SYNC STAGE >>>>>
    for arcid in archive_ids[50:]:
        response, error = await remove_archive_from_category(lrr_client, bookmark_cat_id, arcid, semaphore)
        assert not error, f"Failed to remove archive from category (status {error.status}): {error.error}"
        del response, error
    # <<<<< REMOVE ARCHIVE FROM CATEGORY SYNC STAGE <<<<<

    # >>>>> ADD ARCHIVE TO CATEGORY ASYNC STAGE >>>>>
    add_archive_tasks = []
    for arcid in archive_ids[:50]:
        add_archive_tasks.append(asyncio.create_task(
            add_archive_to_category(lrr_client, bookmark_cat_id, arcid, semaphore)
        ))
    gathered: List[Tuple[AddArchiveToCategoryResponse, LanraragiErrorResponse]] = await asyncio.gather(*add_archive_tasks)
    for response, error in gathered:
        assert not error, f"Failed to add archive to category (status {error.status}): {error.error}"
        del response, error
    # <<<<< ADD ARCHIVE TO CATEGORY ASYNC STAGE <<<<<

    # >>>>> GET CATEGORY ASYNC STAGE >>>>>
    response, error = await lrr_client.category_api.get_category(GetCategoryRequest(category_id=bookmark_cat_id))
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    assert len(response.archives) == 50, "Number of archives in bookmark category does not equal 50!"
    assert set(response.archives) == set(archive_ids[:50]), "Archives in bookmark category do not match!"
    del response, error
    # <<<<< GET CATEGORY ASYNC STAGE <<<<<

    # >>>>> ARCHIVE CATEGORY MEMBERSHIP >>>>>
    response, error = await lrr_client.archive_api.get_archive_categories(GetArchiveCategoriesRequest(arcid=archive_ids[25]))
    assert not error, f"Failed to get archive categories (status {error.status}): {error.error}"
    cat_ids_in2 = {c.category_id for c in response.categories}
    assert bookmark_cat_id in cat_ids_in2, "Archive expected in bookmark category after async add was not reported"
    del response, error
    # <<<<< ARCHIVE CATEGORY MEMBERSHIP <<<<<

    # >>>>> GET DATABASE BACKUP STAGE >>>>>
    response, error = await lrr_client.database_api.get_database_backup()
    assert not error, f"Failed to get database backup (status {error.status}): {error.error}"
    assert len(response.archives) == num_archives, "Number of archives in database backup does not equal number uploaded!"
    assert len(response.categories) == 1, "Number of categories in database backup does not equal 1!"
    assert len(response.categories[0].archives) == 50, "Number of archives in bookmark category does not equal 50!"
    del response, error
    # <<<<< GET DATABASE BACKUP STAGE <<<<<

    # >>>>> REMOVE ARCHIVE FROM CATEGORY ASYNC STAGE >>>>>
    remove_archive_tasks = []
    for arcid in archive_ids[:50]:
        remove_archive_tasks.append(asyncio.create_task(
            remove_archive_from_category(lrr_client, bookmark_cat_id, arcid, semaphore)
        ))
    gathered: List[Tuple[LanraragiResponse, LanraragiErrorResponse]] = await asyncio.gather(*remove_archive_tasks)
    for response, error in gathered:
        assert not error, f"Failed to remove archive from category (status {error.status}): {error.error}"
        del response, error
    # <<<<< REMOVE ARCHIVE FROM CATEGORY ASYNC STAGE <<<<<

    # >>>>> GET CATEGORY STAGE >>>>>
    response, error = await lrr_client.category_api.get_category(GetCategoryRequest(category_id=bookmark_cat_id))
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    assert len(response.archives) == 0, "Number of archives in bookmark category does not equal 0!"
    del response, error
    # <<<<< GET CATEGORY STAGE <<<<<

    # >>>>> VERIFY ARCHIVE CATEGORY MEMBERSHIP CLEARED VIA ARCHIVE API >>>>>
    resp_cleared, err_cleared = await lrr_client.archive_api.get_archive_categories(GetArchiveCategoriesRequest(arcid=archive_ids[25]))
    assert not err_cleared, f"Failed to get archive categories (status {err_cleared.status}): {err_cleared.error}"
    cat_ids_cleared = {c.category_id for c in resp_cleared.categories}
    assert bookmark_cat_id not in cat_ids_cleared, "Archive still reported in bookmark category after removal"
    del resp_cleared, err_cleared
    # <<<<< VERIFY ARCHIVE CATEGORY MEMBERSHIP CLEARED VIA ARCHIVE API <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_shinobu_api(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Very basic functional test of Shinobu API. Does not test concurrent API calls against shinobu.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<
    
    # >>>>> GET SHINOBU STATUS STAGE >>>>>
    response, error = await lrr_client.shinobu_api.get_shinobu_status()
    assert not error, f"Failed to get shinobu status (status {error.status}): {error.error}"
    assert response.is_alive, "Shinobu should be running!"
    pid = response.pid
    del response, error
    # <<<<< GET SHINOBU STATUS STAGE <<<<<

    # >>>>> RESTART SHINOBU STAGE >>>>>
    # restarting shinobu does not guarantee that pid will change (though it is extremely unlikely), so we do it 3 times.
    pid_has_changed = False
    for _ in range(3):
        response, error = await lrr_client.shinobu_api.restart_shinobu()
        assert not error, f"Failed to restart shinobu (status {error.status}): {error.error}"
        if response.new_pid == pid:
            LOGGER.warning(f"Shinobu PID {pid} did not change; retrying...")
            continue
        else:
            pid_has_changed = True
            break
    del response, error
    assert pid_has_changed, "Shinobu restarted 3 times but PID did not change???"
    # <<<<< RESTART SHINOBU STAGE <<<<<

    # >>>>> STOP SHINOBU STAGE >>>>>
    response, error = await lrr_client.shinobu_api.stop_shinobu()
    assert not error, f"Failed to stop shinobu (status {error.status}): {error.error}"
    del response, error
    # <<<<< STOP SHINOBU STAGE <<<<<

    # >>>>> GET SHINOBU STATUS STAGE >>>>>
    # shinobu may not stop immediately.
    retry_count = 0
    max_retries = 3
    has_stopped = False
    while retry_count < max_retries:
        response, error = await lrr_client.shinobu_api.get_shinobu_status()
        assert not error, f"Failed to get shinobu status (status {error.status}): {error.error}"
        if response.is_alive:
            LOGGER.warning(f"Shinobu is still running; retrying in 1s... ({retry_count+1}/{max_retries})")
            retry_count += 1
            await asyncio.sleep(1)
            continue
        else:
            has_stopped = True
            break
    assert has_stopped, "Shinobu did not stop after 3 retries!"
    del response, error
    # <<<<< GET SHINOBU STATUS STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_database_api(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    Very basic functional test of the database API.
    Does not test drop database or get backup.
    """
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
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

    # >>>>> GET STATISTICS STAGE >>>>>
    response, error = await lrr_client.database_api.get_database_stats(GetDatabaseStatsRequest())
    assert not error, f"Failed to get statistics (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET STATISTICS STAGE <<<<<

    # >>>>> CLEAN DATABASE STAGE >>>>>
    response, error = await lrr_client.database_api.clean_database()
    assert not error, f"Failed to clean database (status {error.status}): {error.error}"
    del response, error
    # <<<<< CLEAN DATABASE STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_drop_database(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test drop database API by dropping database and verifying that client has no permissions.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> DROP DATABASE STAGE >>>>>
    response, error = await lrr_client.database_api.drop_database()
    assert not error, f"Failed to drop database (status {error.status}): {error.error}"
    del response, error
    # <<<<< DROP DATABASE STAGE <<<<<
    
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.shinobu_api.get_shinobu_status()
    assert error and error.status == 401, f"Expected no permissions, got status {error.status}."
    # <<<<< TEST CONNECTION STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_tankoubon_api(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    Very basic functional test of the tankoubon API.
    """
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
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

    # >>>>> GET ARCHIVE IDS STAGE >>>>>
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    archive_ids = [arc.arcid for arc in response.data]
    del response, error
    # <<<<< GET ARCHIVE IDS STAGE <<<<<

    # >>>>> CREATE TANKOUBON STAGE >>>>>
    response, error = await lrr_client.tankoubon_api.create_tankoubon(CreateTankoubonRequest(name="Test Tankoubon"))
    assert not error, f"Failed to create tankoubon (status {error.status}): {error.error}"
    tankoubon_id = response.tank_id
    del response, error
    # <<<<< CREATE TANKOUBON STAGE <<<<<

    # >>>>> ADD ARCHIVE TO TANKOUBON STAGE >>>>>
    for i in range(20):
        response, error = await lrr_client.tankoubon_api.add_archive_to_tankoubon(AddArchiveToTankoubonRequest(tank_id=tankoubon_id, arcid=archive_ids[i]))
        assert not error, f"Failed to add archive to tankoubon (status {error.status}): {error.error}"
        del response, error
    # <<<<< ADD ARCHIVE TO TANKOUBON STAGE <<<<<

    # >>>>> GET TANKOUBON STAGE >>>>>
    response, error = await lrr_client.tankoubon_api.get_tankoubon(GetTankoubonRequest(tank_id=tankoubon_id))
    assert not error, f"Failed to get tankoubon (status {error.status}): {error.error}"
    assert set(response.result.archives) == set(archive_ids[:20])
    del response, error
    # <<<<< GET TANKOUBON STAGE <<<<<

    # >>>>> REMOVE ARCHIVE FROM TANKOUBON STAGE >>>>>
    for i in range(20):
        response, error = await lrr_client.tankoubon_api.remove_archive_from_tankoubon(RemoveArchiveFromTankoubonRequest(tank_id=tankoubon_id, arcid=archive_ids[i]))
        assert not error, f"Failed to remove archive from tankoubon (status {error.status}): {error.error}"
        del response, error
    # <<<<< REMOVE ARCHIVE FROM TANKOUBON STAGE <<<<<

    # >>>>> GET TANKOUBON STAGE >>>>>
    response, error = await lrr_client.tankoubon_api.get_tankoubon(GetTankoubonRequest(tank_id=tankoubon_id))
    assert not error, f"Failed to get tankoubon (status {error.status}): {error.error}"
    assert response.result.archives == []
    del response, error
    # <<<<< GET TANKOUBON STAGE <<<<<

    # >>>>> UPDATE TANKOUBON STAGE >>>>>
    response, error = await lrr_client.tankoubon_api.update_tankoubon(UpdateTankoubonRequest(
        tank_id=tankoubon_id, archives=archive_ids[20:40],
        metadata=TankoubonMetadata(name="Updated Tankoubon")
    ))
    assert not error, f"Failed to update tankoubon (status {error.status}): {error.error}"
    del response, error
    # <<<<< UPDATE TANKOUBON STAGE <<<<<

    # >>>>> GET TANKOUBON STAGE >>>>>
    response, error = await lrr_client.tankoubon_api.get_tankoubon(GetTankoubonRequest(tank_id=tankoubon_id))
    assert not error, f"Failed to get tankoubon (status {error.status}): {error.error}"
    assert response.result.name == "Updated Tankoubon"
    assert set(response.result.archives) == set(archive_ids[20:40])
    del response, error
    # <<<<< GET TANKOUBON STAGE <<<<<

    # >>>>> DELETE TANKOUBON STAGE >>>>>
    response, error = await lrr_client.tankoubon_api.delete_tankoubon(DeleteTankoubonRequest(tank_id=tankoubon_id))
    assert not error, f"Failed to delete tankoubon (status {error.status}): {error.error}"
    del response, error
    # <<<<< DELETE TANKOUBON STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_misc_api(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    Basic functional test of miscellaneous API.
    """
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
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

    # >>>>> GET ARCHIVE IDS STAGE >>>>>
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    archive_ids = [arc.arcid for arc in response.data]
    del response, error
    # <<<<< GET ARCHIVE IDS STAGE <<<<<

    # >>>>> GET AVAILABLE PLUGINS STAGE >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="all"))
    assert not error, f"Failed to get available plugins (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET AVAILABLE PLUGINS STAGE <<<<<

    # >>>>> GET OPDS CATALOG STAGE >>>>>
    response, error = await lrr_client.misc_api.get_opds_catalog(GetOpdsCatalogRequest(arcid=archive_ids[0]))
    assert not error, f"Failed to get opds catalog (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET OPDS CATALOG STAGE <<<<<

    # >>>>> CLEAN TEMP FOLDER STAGE >>>>>
    response, error = await lrr_client.misc_api.clean_temp_folder()
    assert not error, f"Failed to clean temp folder (status {error.status}): {error.error}"
    del response, error
    # <<<<< CLEAN TEMP FOLDER STAGE <<<<<

    # >>>>> REGENERATE THUMBNAILS STAGE >>>>>
    response, error = await lrr_client.misc_api.regenerate_thumbnails(RegenerateThumbnailRequest())
    assert not error, f"Failed to regenerate thumbnails (status {error.status}): {error.error}"
    del response, error
    # <<<<< REGENERATE THUMBNAILS STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_minion_api(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator, environment: AbstractLRRDeploymentContext):
    """
    Very basic functional test of the minion API.
    """
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
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
    
    # >>>>> REGENERATE THUMBNAILS STAGE >>>>>
    # to get a job id
    response, error = await lrr_client.misc_api.regenerate_thumbnails(RegenerateThumbnailRequest())
    assert not error, f"Failed to regenerate thumbnails (status {error.status}): {error.error}"
    job_id = response.job
    del response, error
    # <<<<< REGENERATE THUMBNAILS STAGE <<<<<

    # >>>>> GET MINION JOB STATUS STAGE >>>>>
    response, error = await lrr_client.minion_api.get_minion_job_status(GetMinionJobStatusRequest(job_id=job_id))
    assert not error, f"Failed to get minion job status (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET MINION JOB STATUS STAGE <<<<<

    # >>>>> GET MINION JOB DETAILS STAGE >>>>>
    response, error = await lrr_client.minion_api.get_minion_job_details(GetMinionJobDetailRequest(job_id=job_id))
    assert not error, f"Failed to get minion job details (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET MINION JOB DETAILS STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.asyncio
@pytest.mark.experimental
async def test_openapi_invalid_request(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Verify that OpenAPI request validation works.
    """
    # test get archive metadata API.
    # Even if the archive doesn't exist, this request shouldn't go through due to invalid arcid format (40-char req).
    status, content = await lrr_client.handle_request(
        http.HTTPMethod.GET, lrr_client.build_url("/api/archives/123"), lrr_client.headers
    )
    assert status == 400, f"Expected bad request status from malformed arcid, got {status}"
    assert "String is too short" in content, f"Expected \"String is too short\" in response, got: {content}"

    # no error logs
    expect_no_error_logs(environment)

@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_concurrent_clients(environment: AbstractLRRDeploymentContext):
    """
    Example test that shows how to use multiple client instances
    with a shared session for better performance.
    """
    session = aiohttp.ClientSession()
    try:
        client1 = LRRClient(
            lrr_base_url=f"http://127.0.0.1:{environment.lrr_port}",
            lrr_api_key="lanraragi",
            client_session=session
        )
        client2 = LRRClient(
            lrr_base_url=f"http://127.0.0.1:{environment.lrr_port}",
            lrr_api_key="lanraragi",
            client_session=session
        )
        results = await asyncio.gather(
            client1.misc_api.get_server_info(),
            client2.category_api.get_all_categories()
        )
        for _, error in results:
            assert not error, f"Failed to get server info (status {error.status}): {error.error}"
    finally:
        await session.close()

# skip: for demonstration purposes only.
@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.experimental
async def test_webkit_search_bar(lrr_client: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator):
    """
    Upload two archive, apply search filter, read archive, then go back and check the search filter is still populated.
    """
    num_archives = 2

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
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
        for title, tags, wr in [
            ("Test Archive", "tag-1,tag-2", write_responses[0]),
            ("Test Archive 2", "tag-2,tag-3", write_responses[1])
        ]:
            _, error = await upload_archive(lrr_client, wr.save_path, wr.save_path.name, semaphore, title=title, tags=tags)
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.webkit.launch()
        page = await browser.new_page()
        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        assert await page.title() == LRR_INDEX_TITLE

        # enter admin portal
        # exit overlay
        if "New Version Release Notes" in await page.content():
            LOGGER.info("Closing new releases overlay.")
            await page.keyboard.press("Escape")

        # click search bar
        LOGGER.info("Applying search filter: \"tag-1\"...")
        await page.get_by_role("combobox", name="Search Title, Artist, Series").click()
        await page.get_by_role("combobox", name="Search Title, Artist, Series").fill("tag-1")
        await page.get_by_role("button", name="Apply Filter").click()

        LOGGER.info("Opening reader for \"Test Archive\"...")
        await page.get_by_role("link", name="Test Archive").nth(1).click()

        LOGGER.info("Going back to index page and checking search bar...")
        await page.get_by_role("link", name="").click()
        await playwright.async_api.expect(
            page.get_by_role("combobox", name="Search Title, Artist, Series")
        ).to_have_value("tag-1")
    # <<<<< UI STAGE <<<<<