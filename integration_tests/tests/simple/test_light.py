"""
Lightweight API integration tests for the LANraragi server.

These tests don't require bulk archive uploads and focus on server/system functionality.
No xfail cache priming is needed since these tests don't stress the server with concurrent requests.

For each testing pipeline, a corresponding LRR environment is set up and torn down.
"""

import asyncio
import http
import logging
from pathlib import Path
import sys
import tempfile

import aiohttp
import numpy as np
import pytest
import playwright.async_api

from lanraragi.clients.client import LRRClient
from lanraragi.models.category import (
    CreateCategoryRequest,
    DeleteCategoryRequest,
    GetCategoryRequest,
    UpdateBookmarkLinkRequest,
    UpdateCategoryRequest,
)

from aio_lanraragi_tests.helpers import (
    expect_no_error_logs,
    save_archives,
    upload_archive,
)
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import LRR_INDEX_TITLE

LOGGER = logging.getLogger(__name__)


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
        await page.get_by_role("link", name="").click()
        await playwright.async_api.expect(
            page.get_by_role("combobox", name="Search Title, Artist, Series")
        ).to_have_value("tag-1")
        await browser.close()
    # <<<<< UI STAGE <<<<<
