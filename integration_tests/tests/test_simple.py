"""
Collection of all simple API testing pipelines for the LANraragi server in a Docker environment.

For each testing pipeline, a new network, server and database are allocated and reclaimed.
This provides every test with an isolated environment.
"""

import asyncio
import logging
import os
from pathlib import Path
import tempfile
from typing import List, Tuple
import aiohttp.client_exceptions
import docker
import numpy as np
import pytest
import pytest_asyncio
import aiohttp
from urllib.parse import urlparse, parse_qs
from lanraragi.clients.client import LRRClient
from aio_lanraragi_tests.lrr_docker import LRREnvironment
from aio_lanraragi_tests.common import compute_upload_checksum
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.models import (
    CreatePageRequest,
    WriteArchiveRequest,
    WriteArchiveResponse,
)
from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.metadata import create_tag_generators, get_tag_assignments
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.archive import (
    ClearNewArchiveFlagRequest,
    DeleteArchiveRequest,
    DeleteArchiveResponse,
    ExtractArchiveRequest,
    GetArchiveMetadataRequest,
    GetArchiveThumbnailRequest,
    UpdateArchiveThumbnailRequest,
    UpdateReadingProgressionRequest,
    UploadArchiveRequest,
    UploadArchiveResponse,
)
from lanraragi.models.base import (
    LanraragiErrorResponse,
    LanraragiResponse
)
from lanraragi.models.category import (
    AddArchiveToCategoryRequest,
    AddArchiveToCategoryResponse,
    CreateCategoryRequest,
    DeleteCategoryRequest,
    GetCategoryRequest,
    GetCategoryResponse,
    RemoveArchiveFromCategoryRequest,
    UpdateBookmarkLinkRequest,
    UpdateCategoryRequest
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
from lanraragi.models.search import (
    GetRandomArchivesRequest,
    SearchArchiveIndexRequest
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

logger = logging.getLogger(__name__)

@pytest.fixture
def docker_environment(request: pytest.FixtureRequest):
    build_path: str = request.config.getoption("--build")
    image: str = request.config.getoption("--image")
    git_url: str = request.config.getoption("--git-url")
    git_branch: str = request.config.getoption("--git-branch")
    use_docker_api: bool = request.config.getoption("--docker-api")
    docker_client = docker.from_env()
    docker_api = docker.APIClient(base_url="unix://var/run/docker.sock") if use_docker_api else None
    environment = LRREnvironment(
        build_path, image, git_url, git_branch, docker_client, docker_api=docker_api,
        init_with_allow_uploads=True, init_with_api_key=True, init_with_nofunmode=True
    )
    environment.setup()
    request.session.lrr_environment = environment # Store environment in pytest session for access in hooks
    yield
    environment.teardown()

@pytest.fixture
def semaphore():
    yield asyncio.BoundedSemaphore(value=8)

@pytest_asyncio.fixture
async def lanraragi():
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    client = LRRClient(
        lrr_host="http://localhost:3001",
        lrr_api_key="lanraragi",
        timeout=10
    )
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
                timeout_msg = f"Request timed out after {client.session.timeout.total}s"
                logger.error(f"Failed to get page {page_api} (timeout): {timeout_msg}")
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

async def upload_archive(client: LRRClient, save_path: Path, filename: str, semaphore: asyncio.Semaphore, checksum: str=None, title: str=None, tags: str=None) -> Tuple[UploadArchiveResponse, LanraragiErrorResponse]:
    async with semaphore:
        with open(save_path, 'rb') as f:  # noqa: ASYNC230
            file = f.read()
            request = UploadArchiveRequest(file=file, filename=filename, title=title, tags=tags, file_checksum=checksum)
        return await client.archive_api.upload_archive(request)

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

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_archive_upload(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
    """
    Creates 100 archives to upload to the LRR server,
    and verifies that this number of archives is correct.

    Then deletes 50 archives (5 sequentially, followed by
    45 concurrently). Verifies archive count is correct.
    """
    generator = np.random.default_rng(42)
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    logger.debug("Established connection with test LRR server.")
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
        logger.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, generator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        logger.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, generator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> VALIDATE UPLOAD COUNT STAGE >>>>>
    logger.debug("Validating upload counts.")
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"

    # get this data for archive deletion.
    arcs_delete_sync = response.data[:5]
    arcs_delete_async = response.data[5:50]
    assert len(response.data) == num_archives, "Number of archives on server does not equal number uploaded!"
    # <<<<< VALIDATE UPLOAD COUNT STAGE <<<<<

    # >>>>> GET DATABASE BACKUP STAGE >>>>>
    response, error = await lanraragi.database_api.get_database_backup()
    assert not error, f"Failed to get database backup (status {error.status}): {error.error}"
    assert len(response.archives) == num_archives, "Number of archives in database backup does not equal number uploaded!"
    del response, error
    # <<<<< GET DATABASE BACKUP STAGE <<<<<

    # >>>>> DELETE ARCHIVE SYNC STAGE >>>>>
    for archive in arcs_delete_sync:
        response, error = await lanraragi.archive_api.delete_archive(DeleteArchiveRequest(arcid=archive.arcid))
        assert not error, f"Failed to delete archive {archive.arcid} with status {error.status} and error: {error.error}"
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
    assert len(response.data) == 100-5, "Incorrect number of archives in server!"
    # <<<<< DELETE ARCHIVE SYNC STAGE <<<<<

    # >>>>> DELETE ARCHIVE ASYNC STAGE >>>>>
    tasks = []
    for archive in arcs_delete_async:
        tasks.append(asyncio.create_task(delete_archive(lanraragi, archive.arcid, semaphore)))
    gathered: List[Tuple[DeleteArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
    for response, error in gathered:
        assert not error, f"Delete archive failed (status {error.status}): {error.error}"
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
    assert len(response.data) == 100-50, "Incorrect number of archives in server!"
    # <<<<< DELETE ARCHIVE ASYNC STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_archive_read(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
    """
    Simulates a read archive operation.
    """
    generator = np.random.default_rng(42)
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    logger.debug("Established connection with test LRR server.")
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
        logger.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, generator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        logger.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, generator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> GET ALL ARCHIVES STAGE >>>>>
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == num_archives, "Number of archives on server does not equal number uploaded!"
    first_archive_id = response.data[0].arcid

    # >>>>> TEST THUMBNAIL STAGE >>>>>
    response, error = await lanraragi.archive_api.get_archive_thumbnail(GetArchiveThumbnailRequest(arcid=first_archive_id, nofallback=True))
    assert not error, f"Failed to get thumbnail with no_fallback=True (status {error.status}): {error.error}"
    del response, error

    response, error = await lanraragi.archive_api.get_archive_thumbnail(GetArchiveThumbnailRequest(arcid=first_archive_id))
    assert not error, f"Failed to get thumbnail with default settings (status {error.status}): {error.error}"
    assert response.content is not None, "Thumbnail content should not be None with default settings"
    assert response.content_type is not None, "Expected content_type to be set in regular response"
    del response, error
    # <<<<< TEST THUMBNAIL STAGE <<<<<

    # >>>>> UPDATE ARCHIVE THUMBNAIL STAGE >>>>>
    response, error = await retry_on_lock(lambda: lanraragi.archive_api.update_thumbnail(UpdateArchiveThumbnailRequest(arcid=first_archive_id, page=2)))
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
    tasks.append(asyncio.create_task(retry_on_lock(lambda: lanraragi.archive_api.clear_new_archive_flag(ClearNewArchiveFlagRequest(arcid=first_archive_id)))))
    tasks.append(asyncio.create_task(retry_on_lock(lambda: lanraragi.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=first_archive_id)))))
    tasks.append(asyncio.create_task(get_bookmark_category_detail(lanraragi, semaphore)))
    tasks.append(asyncio.create_task(load_pages_from_archive(lanraragi, first_archive_id, semaphore)))
    tasks.append(asyncio.create_task(retry_on_lock(lambda: lanraragi.archive_api.update_reading_progression(UpdateReadingProgressionRequest(arcid=first_archive_id, page=1)))))
    tasks.append(asyncio.create_task(retry_on_lock(lambda: lanraragi.archive_api.get_archive_thumbnail(GetArchiveThumbnailRequest(arcid=first_archive_id)))))

    results: List[Tuple[LanraragiResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
    for response, error in results:
        assert not error, f"Failed to complete task (status {error.status}): {error.error}"
    # <<<<< SIMULATE READ ARCHIVE STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_category(lanraragi: LRRClient):
    """
    Runs sanity tests against the category and bookmark link API.

    TODO: a more comprehensive test should be designed to verify that the first-time installation
    does not apply when a server is restarted. This should preferably be in a separate test module
    that is more involved with the server environment.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    logger.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    response, error = await lanraragi.category_api.get_all_categories()
    assert not error, f"Failed to get all categories (status {error.status}): {error.error}"
    assert len(response.data) == 1, "Server does not contain exactly the bookmark category!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> GET BOOKMARK LINK >>>>>
    response, error = await lanraragi.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    category_id = response.category_id
    response, error = await lanraragi.category_api.get_category(GetCategoryRequest(category_id=category_id))
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    category_name = response.name
    assert category_name == '🔖 Favorites', "Bookmark is not linked to Favorites!"
    del response, error
    # <<<<< GET BOOKMARK LINK <<<<<

    # >>>>> CREATE CATEGORY >>>>>
    request = CreateCategoryRequest(name="test-static-category")
    response, error = await lanraragi.category_api.create_category(request)
    assert not error, f"Failed to create static category (status {error.status}): {error.error}"
    static_cat_id = response.category_id
    request = CreateCategoryRequest(name="test-dynamic-category", search="language:english")
    response, error = await lanraragi.category_api.create_category(request)
    assert not error, f"Failed to create dynamic category (status {error.status}): {error.error}"
    dynamic_cat_id = response.category_id
    del request, response, error
    # <<<<< CREATE CATEGORY <<<<<

    # >>>>> UPDATE CATEGORY >>>>>
    request = UpdateCategoryRequest(category_id=static_cat_id, name="test-static-category-changed")
    response, error = await lanraragi.category_api.update_category(request)
    assert not error, f"Failed to update category (status {error.status}): {error.error}"
    request = GetCategoryRequest(category_id=static_cat_id)
    response, error = await lanraragi.category_api.get_category(request)
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    assert response.name == "test-static-category-changed", "Category name is incorrect after update!"
    del request, response, error
    # <<<<< UPDATE CATEGORY <<<<<

    # >>>>> UPDATE BOOKMARK LINK >>>>>
    request = UpdateBookmarkLinkRequest(category_id=static_cat_id)
    response, error = await lanraragi.category_api.update_bookmark_link(request)
    assert not error, f"Failed to update bookmark link (status {error.status}): {error.error}"
    request = UpdateBookmarkLinkRequest(category_id=dynamic_cat_id)
    response, error = await lanraragi.category_api.update_bookmark_link(request)
    assert error and error.status == 400, "Assigning bookmark link to dynamic category should not be possible!"
    response, error = await lanraragi.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    # <<<<< UPDATE BOOKMARK LINK <<<<<

    # >>>>> DELETE BOOKMARK LINK >>>>>
    response, error = await lanraragi.category_api.disable_bookmark_feature()
    assert not error, f"Failed to disable bookmark link (status {error.status}): {error.error}"
    response, error = await lanraragi.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    assert not response.category_id, "Bookmark link should be empty after disabling!"
    # <<<<< DELETE BOOKMARK LINK <<<<<

    # >>>>> UNLINK BOOKMARK >>>>>
    request = CreateCategoryRequest(name="test-static-category-2")
    response, error = await lanraragi.category_api.create_category(request)
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    static_cat_id_2 = response.category_id
    del request, response, error
    request = UpdateBookmarkLinkRequest(category_id=static_cat_id_2)
    response, error = await lanraragi.category_api.update_bookmark_link(request)
    assert not error, f"Failed to update bookmark link (status {error.status}): {error.error}"
    # Delete the category that is linked to the bookmark
    request = DeleteCategoryRequest(category_id=static_cat_id_2)
    response, error = await lanraragi.category_api.delete_category(request)
    assert not error, f"Failed to delete category (status {error.status}): {error.error}"
    del request, response, error
    response, error = await lanraragi.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    assert not response.category_id, "Deleting a category linked to bookmark should unlink bookmark!"
    del response, error
    # <<<<< UNLINK BOOKMARK <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_archive_category_interaction(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
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
    9. remove 50 archives from bookmark asynchronously
    10. check that 0 archives are in the bookmark category
    """
    generator = np.random.default_rng(42)
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    logger.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    archive_ids = []
    tag_generators = create_tag_generators(100, pmf)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        logger.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, generator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        logger.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, generator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            archive_ids.append(response.arcid)
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> GET BOOKMARK LINK STAGE >>>>>
    response, error = await lanraragi.category_api.get_bookmark_link()
    assert not error, f"Failed to get bookmark link (status {error.status}): {error.error}"
    bookmark_cat_id = response.category_id
    del response, error
    # <<<<< GET BOOKMARK LINK STAGE <<<<<

    # >>>>> ADD ARCHIVE TO CATEGORY SYNC STAGE >>>>>
    for arcid in archive_ids[50:]:
        response, error = await add_archive_to_category(lanraragi, bookmark_cat_id, arcid, semaphore)
        assert not error, f"Failed to add archive to category (status {error.status}): {error.error}"
        del response, error
    # <<<<< ADD ARCHIVE TO CATEGORY SYNC STAGE <<<<<

    # >>>>> GET CATEGORY SYNC STAGE >>>>>
    response, error = await lanraragi.category_api.get_category(GetCategoryRequest(category_id=bookmark_cat_id))
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    assert len(response.archives) == 50, "Number of archives in bookmark category does not equal 50!"
    assert set(response.archives) == set(archive_ids[50:]), "Archives in bookmark category do not match!"
    del response, error
    # <<<<< GET CATEGORY SYNC STAGE <<<<<

    # >>>>> REMOVE ARCHIVE FROM CATEGORY SYNC STAGE >>>>>
    for arcid in archive_ids[50:]:
        response, error = await remove_archive_from_category(lanraragi, bookmark_cat_id, arcid, semaphore)
        assert not error, f"Failed to remove archive from category (status {error.status}): {error.error}"
        del response, error
    # <<<<< REMOVE ARCHIVE FROM CATEGORY SYNC STAGE <<<<<

    # >>>>> ADD ARCHIVE TO CATEGORY ASYNC STAGE >>>>>
    add_archive_tasks = []
    for arcid in archive_ids[:50]:
        add_archive_tasks.append(asyncio.create_task(
            add_archive_to_category(lanraragi, bookmark_cat_id, arcid, semaphore)
        ))
    gathered: List[Tuple[AddArchiveToCategoryResponse, LanraragiErrorResponse]] = await asyncio.gather(*add_archive_tasks)
    for response, error in gathered:
        assert not error, f"Failed to add archive to category (status {error.status}): {error.error}"
        del response, error
    # <<<<< ADD ARCHIVE TO CATEGORY ASYNC STAGE <<<<<

    # >>>>> GET CATEGORY ASYNC STAGE >>>>>
    response, error = await lanraragi.category_api.get_category(GetCategoryRequest(category_id=bookmark_cat_id))
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    assert len(response.archives) == 50, "Number of archives in bookmark category does not equal 50!"
    assert set(response.archives) == set(archive_ids[:50]), "Archives in bookmark category do not match!"
    del response, error
    # <<<<< GET CATEGORY ASYNC STAGE <<<<<

    # >>>>> GET DATABASE BACKUP STAGE >>>>>
    response, error = await lanraragi.database_api.get_database_backup()
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
            remove_archive_from_category(lanraragi, bookmark_cat_id, arcid, semaphore)
        ))
    gathered: List[Tuple[LanraragiResponse, LanraragiErrorResponse]] = await asyncio.gather(*remove_archive_tasks)
    for response, error in gathered:
        assert not error, f"Failed to remove archive from category (status {error.status}): {error.error}"
        del response, error
    # <<<<< REMOVE ARCHIVE FROM CATEGORY ASYNC STAGE <<<<<

    # >>>>> GET CATEGORY STAGE >>>>>
    response, error = await lanraragi.category_api.get_category(GetCategoryRequest(category_id=bookmark_cat_id))
    assert not error, f"Failed to get category (status {error.status}): {error.error}"
    assert len(response.archives) == 0, "Number of archives in bookmark category does not equal 0!"
    del response, error
    # <<<<< GET CATEGORY STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_search_api(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
    """
    Very basic functional test of the search API.
    
    1. upload 100 archives
    2. search for 20 archives using the search API
    3. search for 20 archives using random search API
    4. search for 20 archives using random search API with newonly=true
    5. search for 20 archives using random search API with untaggedonly=true (should return empty)
    """
    generator = np.random.default_rng(42)
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    logger.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    tag_generators = create_tag_generators(num_archives, pmf)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        logger.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, generator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        logger.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, generator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> SEARCH STAGE >>>>>
    # TODO: current test design limits ability to test results of search (e.g. tag filtering), will need to unravel logic for better test transparency
    response, error = await lanraragi.search_api.search_archive_index(SearchArchiveIndexRequest())
    assert not error, f"Failed to search archive index (status {error.status}): {error.error}"
    assert len(response.data) == 100
    del response, error

    response, error = await lanraragi.search_api.get_random_archives(GetRandomArchivesRequest(count=20))
    assert not error, f"Failed to get random archives (status {error.status}): {error.error}"
    assert len(response.data) == 20
    del response, error

    response, error = await lanraragi.search_api.get_random_archives(GetRandomArchivesRequest(count=20, newonly=True))
    assert not error, f"Failed to get random archives (status {error.status}): {error.error}"
    assert len(response.data) == 20
    del response, error

    response, error = await lanraragi.search_api.get_random_archives(GetRandomArchivesRequest(count=20, untaggedonly=True))
    assert not error, f"Failed to get random archives (status {error.status}): {error.error}"
    assert len(response.data) == 0
    del response, error
    # <<<<< SEARCH STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_shinobu_api(lanraragi: LRRClient):
    """
    Very basic functional test of Shinobu API. Does not test concurrent API calls against shinobu.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    logger.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<
    
    # >>>>> GET SHINOBU STATUS STAGE >>>>>
    response, error = await lanraragi.shinobu_api.get_shinobu_status()
    assert not error, f"Failed to get shinobu status (status {error.status}): {error.error}"
    assert response.is_alive, "Shinobu should be running!"
    pid = response.pid
    del response, error
    # <<<<< GET SHINOBU STATUS STAGE <<<<<

    # >>>>> RESTART SHINOBU STAGE >>>>>
    # restarting shinobu does not guarantee that pid will change (though it is extremely unlikely), so we do it 3 times.
    pid_has_changed = False
    for _ in range(3):
        response, error = await lanraragi.shinobu_api.restart_shinobu()
        assert not error, f"Failed to restart shinobu (status {error.status}): {error.error}"
        if response.new_pid == pid:
            logger.warning(f"Shinobu PID {pid} did not change; retrying...")
            continue
        else:
            pid_has_changed = True
            break
    del response, error
    assert pid_has_changed, "Shinobu restarted 3 times but PID did not change???"
    # <<<<< RESTART SHINOBU STAGE <<<<<

    # >>>>> STOP SHINOBU STAGE >>>>>
    response, error = await lanraragi.shinobu_api.stop_shinobu()
    assert not error, f"Failed to stop shinobu (status {error.status}): {error.error}"
    del response, error
    # <<<<< STOP SHINOBU STAGE <<<<<

    # >>>>> GET SHINOBU STATUS STAGE >>>>>
    response, error = await lanraragi.shinobu_api.get_shinobu_status()
    assert not error, f"Failed to get shinobu status (status {error.status}): {error.error}"
    assert not response.is_alive, "Shinobu should be stopped!"
    del response, error
    # <<<<< GET SHINOBU STATUS STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_database_api(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
    """
    Very basic functional test of the database API.
    Does not test drop database or get backup.
    """
    generator = np.random.default_rng(42)
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    logger.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<
    
    # >>>>> UPLOAD STAGE >>>>>
    tag_generators = create_tag_generators(num_archives, pmf)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        logger.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, generator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        logger.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, generator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> GET STATISTICS STAGE >>>>>
    response, error = await lanraragi.database_api.get_database_stats(GetDatabaseStatsRequest())
    assert not error, f"Failed to get statistics (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET STATISTICS STAGE <<<<<

    # >>>>> CLEAN DATABASE STAGE >>>>>
    response, error = await lanraragi.database_api.clean_database()
    assert not error, f"Failed to clean database (status {error.status}): {error.error}"
    del response, error
    # <<<<< CLEAN DATABASE STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_drop_database(lanraragi: LRRClient):
    """
    Test drop database API by dropping database and verifying that client has no permissions.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    logger.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> DROP DATABASE STAGE >>>>>
    response, error = await lanraragi.database_api.drop_database()
    assert not error, f"Failed to drop database (status {error.status}): {error.error}"
    del response, error
    # <<<<< DROP DATABASE STAGE <<<<<
    
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.shinobu_api.get_shinobu_status()
    assert error and error.status == 401, f"Expected no permissions, got status {error.status}."
    # <<<<< TEST CONNECTION STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_tankoubon_api(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
    """
    Very basic functional test of the tankoubon API.
    """
    generator = np.random.default_rng(42)
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    logger.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<
    
    # >>>>> UPLOAD STAGE >>>>>
    tag_generators = create_tag_generators(num_archives, pmf)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        logger.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, generator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        logger.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, generator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> GET ARCHIVE IDS STAGE >>>>>
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    archive_ids = [arc.arcid for arc in response.data]
    del response, error
    # <<<<< GET ARCHIVE IDS STAGE <<<<<

    # >>>>> CREATE TANKOUBON STAGE >>>>>
    response, error = await lanraragi.tankoubon_api.create_tankoubon(CreateTankoubonRequest(name="Test Tankoubon"))
    assert not error, f"Failed to create tankoubon (status {error.status}): {error.error}"
    tankoubon_id = response.tank_id
    del response, error
    # <<<<< CREATE TANKOUBON STAGE <<<<<

    # >>>>> ADD ARCHIVE TO TANKOUBON STAGE >>>>>
    for i in range(20):
        response, error = await lanraragi.tankoubon_api.add_archive_to_tankoubon(AddArchiveToTankoubonRequest(tank_id=tankoubon_id, arcid=archive_ids[i]))
        assert not error, f"Failed to add archive to tankoubon (status {error.status}): {error.error}"
        del response, error
    # <<<<< ADD ARCHIVE TO TANKOUBON STAGE <<<<<

    # >>>>> GET TANKOUBON STAGE >>>>>
    response, error = await lanraragi.tankoubon_api.get_tankoubon(GetTankoubonRequest(tank_id=tankoubon_id))
    assert not error, f"Failed to get tankoubon (status {error.status}): {error.error}"
    assert set(response.result.archives) == set(archive_ids[:20])
    del response, error
    # <<<<< GET TANKOUBON STAGE <<<<<

    # >>>>> REMOVE ARCHIVE FROM TANKOUBON STAGE >>>>>
    for i in range(20):
        response, error = await lanraragi.tankoubon_api.remove_archive_from_tankoubon(RemoveArchiveFromTankoubonRequest(tank_id=tankoubon_id, arcid=archive_ids[i]))
        assert not error, f"Failed to remove archive from tankoubon (status {error.status}): {error.error}"
        del response, error
    # <<<<< REMOVE ARCHIVE FROM TANKOUBON STAGE <<<<<

    # >>>>> GET TANKOUBON STAGE >>>>>
    response, error = await lanraragi.tankoubon_api.get_tankoubon(GetTankoubonRequest(tank_id=tankoubon_id))
    assert not error, f"Failed to get tankoubon (status {error.status}): {error.error}"
    assert response.result.archives == []
    del response, error
    # <<<<< GET TANKOUBON STAGE <<<<<

    # >>>>> UPDATE TANKOUBON STAGE >>>>>
    response, error = await lanraragi.tankoubon_api.update_tankoubon(UpdateTankoubonRequest(
        tank_id=tankoubon_id, archives=archive_ids[20:40],
        metadata=TankoubonMetadata(name="Updated Tankoubon")
    ))
    assert not error, f"Failed to update tankoubon (status {error.status}): {error.error}"
    del response, error
    # <<<<< UPDATE TANKOUBON STAGE <<<<<

    # >>>>> GET TANKOUBON STAGE >>>>>
    response, error = await lanraragi.tankoubon_api.get_tankoubon(GetTankoubonRequest(tank_id=tankoubon_id))
    assert not error, f"Failed to get tankoubon (status {error.status}): {error.error}"
    assert response.result.name == "Updated Tankoubon"
    assert set(response.result.archives) == set(archive_ids[20:40])
    del response, error
    # <<<<< GET TANKOUBON STAGE <<<<<

    # >>>>> DELETE TANKOUBON STAGE >>>>>
    response, error = await lanraragi.tankoubon_api.delete_tankoubon(DeleteTankoubonRequest(tank_id=tankoubon_id))
    assert not error, f"Failed to delete tankoubon (status {error.status}): {error.error}"
    del response, error
    # <<<<< DELETE TANKOUBON STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_misc_api(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
    """
    Basic functional test of miscellaneous API.
    """
    generator = np.random.default_rng(42)
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    logger.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<
    
    # >>>>> UPLOAD STAGE >>>>>
    tag_generators = create_tag_generators(num_archives, pmf)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        logger.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, generator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        logger.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, generator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> GET ARCHIVE IDS STAGE >>>>>
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    archive_ids = [arc.arcid for arc in response.data]
    del response, error
    # <<<<< GET ARCHIVE IDS STAGE <<<<<

    # >>>>> GET AVAILABLE PLUGINS STAGE >>>>>
    response, error = await lanraragi.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="all"))
    assert not error, f"Failed to get available plugins (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET AVAILABLE PLUGINS STAGE <<<<<

    # >>>>> GET OPDS CATALOG STAGE >>>>>
    response, error = await lanraragi.misc_api.get_opds_catalog(GetOpdsCatalogRequest(arcid=archive_ids[0]))
    assert not error, f"Failed to get opds catalog (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET OPDS CATALOG STAGE <<<<<

    # >>>>> CLEAN TEMP FOLDER STAGE >>>>>
    response, error = await lanraragi.misc_api.clean_temp_folder()
    assert not error, f"Failed to clean temp folder (status {error.status}): {error.error}"
    del response, error
    # <<<<< CLEAN TEMP FOLDER STAGE <<<<<

    # >>>>> REGENERATE THUMBNAILS STAGE >>>>>
    response, error = await lanraragi.misc_api.regenerate_thumbnails(RegenerateThumbnailRequest())
    assert not error, f"Failed to regenerate thumbnails (status {error.status}): {error.error}"
    del response, error
    # <<<<< REGENERATE THUMBNAILS STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_minion_api(lanraragi: LRRClient, semaphore: asyncio.Semaphore):
    """
    Very basic functional test of the minion API.
    """
    generator = np.random.default_rng(42)
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    logger.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<
    
    # >>>>> UPLOAD STAGE >>>>>
    tag_generators = create_tag_generators(num_archives, pmf)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        logger.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, generator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        logger.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, generator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<
    
    # >>>>> REGENERATE THUMBNAILS STAGE >>>>>
    # to get a job id
    response, error = await lanraragi.misc_api.regenerate_thumbnails(RegenerateThumbnailRequest())
    assert not error, f"Failed to regenerate thumbnails (status {error.status}): {error.error}"
    job_id = response.job
    del response, error
    # <<<<< REGENERATE THUMBNAILS STAGE <<<<<

    # >>>>> GET MINION JOB STATUS STAGE >>>>>
    response, error = await lanraragi.minion_api.get_minion_job_status(GetMinionJobStatusRequest(job_id=job_id))
    assert not error, f"Failed to get minion job status (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET MINION JOB STATUS STAGE <<<<<

    # >>>>> GET MINION JOB DETAILS STAGE >>>>>
    response, error = await lanraragi.minion_api.get_minion_job_details(GetMinionJobDetailRequest(job_id=job_id))
    assert not error, f"Failed to get minion job details (status {error.status}): {error.error}"
    del response, error
    # <<<<< GET MINION JOB DETAILS STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Test requires POSIX-compliant OS")
async def test_concurrent_clients():
    """
    Example test that shows how to use multiple client instances
    with a shared session for better performance.
    """
    session = aiohttp.ClientSession()
    try:
        client1 = LRRClient(
            lrr_host="http://localhost:3001",
            lrr_api_key="lanraragi",
            session=session
        )
        client2 = LRRClient(
            lrr_host="http://localhost:3001",
            lrr_api_key="lanraragi",
            session=session
        )
        results = await asyncio.gather(
            client1.misc_api.get_server_info(),
            client2.category_api.get_all_categories()
        )
        for response, error in results:
            assert not error, f"Failed to get server info (status {error.status}): {error.error}"
    finally:
        await session.close()
