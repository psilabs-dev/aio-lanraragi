"""
Any integration test which does involve concurrent uploads.
"""

import asyncio
import logging
from pathlib import Path
import sys
import tempfile
import numpy as np
import pytest

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
from lanraragi.models.base import (
    LanraragiErrorResponse,
    LanraragiResponse
)
from lanraragi.models.category import (
    AddArchiveToCategoryResponse,
    GetCategoryRequest,
)
from lanraragi.models.database import GetDatabaseStatsRequest
from lanraragi.models.minion import (
    GetMinionJobDetailRequest,
    GetMinionJobStatusRequest
)
from lanraragi.models.misc import (
    GetAvailablePluginsRequest,
    GetOpdsCatalogRequest,
    RegenerateThumbnailRequest
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

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext, expect_no_error_logs
from aio_lanraragi_tests.utils.api_wrappers import (
    add_archive_to_category,
    delete_archive,
    get_bookmark_category_detail,
    load_pages_from_archive,
    remove_archive_from_category,
    save_archives,
    upload_archives
)
from aio_lanraragi_tests.utils.concurrency import retry_on_lock
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
    gathered: list[tuple[DeleteArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
    for response, error in gathered:
        assert not error, f"Delete archive failed (status {error.status}): {error.error}"
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
    assert len(response.data) == num_archives-50, "Incorrect number of archives in server!"
    assert len(list(environment.archives_dir.iterdir())) == num_archives-50, "Incorrect number of archives on disk!"
    # <<<<< DELETE ARCHIVE ASYNC STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)

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

    results: list[tuple[LanraragiResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
    for response, error in results:
        assert not error, f"Failed to complete task (status {error.status}): {error.error}"
    # <<<<< SIMULATE READ ARCHIVE STAGE <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)

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
    gathered: list[tuple[AddArchiveToCategoryResponse, LanraragiErrorResponse]] = await asyncio.gather(*add_archive_tasks)
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
    gathered: list[tuple[LanraragiResponse, LanraragiErrorResponse]] = await asyncio.gather(*remove_archive_tasks)
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
    expect_no_error_logs(environment, LOGGER)

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
    expect_no_error_logs(environment, LOGGER)

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
    expect_no_error_logs(environment, LOGGER)

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
    expect_no_error_logs(environment, LOGGER)

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
    expect_no_error_logs(environment, LOGGER)
