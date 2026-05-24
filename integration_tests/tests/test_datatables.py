"""
Tests for datatables-related behavior, including reader-to-index transitions
and cross-DT-page archive navigation.
"""

import asyncio
import json
import logging
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import playwright
import playwright.async_api
import playwright.async_api._generated
import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.common import LRR_INDEX_TITLE
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive
from aio_lanraragi_tests.utils.concurrency import get_bounded_sem
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
    assert_no_spinner,
)

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def resource_prefix(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    yield request.config.getoption("--resource-prefix") + "test_"


@pytest.fixture
def port_offset(request: pytest.FixtureRequest) -> Generator[int, None, None]:
    yield request.config.getoption("--port-offset") + 10


@pytest.fixture
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    """
    Provide an environment with a small DT pagesize for faster testing.
    """
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    try:
        environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)
        environment.set_pagesize(10)

        # configure environments to session
        environments: dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
        request.session.lrr_environments = environments

        yield environment
    finally:
        environment.teardown(remove_data=True)


@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield get_bounded_sem()


@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
    """
    Provides a LRRClient for testing with async cleanup.
    """
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("navigation")
async def test_browser_back_and_forward(
        lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Test browser back/forward across cross-archive navigation, with return-to-index.

    1. Upload 3 archives (3 pages each).
    2. Navigate index -> archive 1 -> archive 2 -> archive 3 via ArrowRight.
    3. Browser back lands on archive 2; forward returns to archive 3.
    4. Browser back three times to reach index; verify index title and that all
       3 archives are visible in the DT.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        for name, title in [("archive-1", "Back 1"), ("archive-2", "Back 2"), ("archive-3", "Back 3")]:
            archive_path = create_archive_file(Path(tmpdir), name, num_pages=3)
            response, error = await upload_archive(
                lrr_client, archive_path, archive_path.name, semaphore, title=title, tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # Navigate to index page.
            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")

            # Collect DT order.
            search_response_body = None
            for resp in responses:
                if "/search" not in resp.url or resp.request.method != "GET" or resp.status != 200:
                    continue
                body = json.loads(await resp.text())
                if "data" in body and len(body["data"]) == 3:
                    search_response_body = body
                    break
            assert search_response_body is not None, "Did not find datatables search response"
            dt_arcids = [entry["arcid"] for entry in search_response_body["data"]]
            dt_titles = [entry["title"] for entry in search_response_body["data"]]
            LOGGER.info(f"DT order: {list(zip(dt_titles, dt_arcids))}")
            responses.clear()
            console_evts.clear()

            # Open first archive from index.
            await page.locator("#thumbs_container a", has_text=dt_titles[0]).first.click()
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            assert f"id={dt_arcids[0]}" in page.url

            # Navigate forward through all 3 archives (3 pages each, transitions at press 2 and 5).
            for i in range(6):
                if i in (2, 5):
                    responses.clear()
                    console_evts.clear()
                await page.keyboard.press("ArrowRight")
                if i in (2, 5):
                    expected_idx = 1 if i == 2 else 2
                    await page.wait_for_url(lambda url, eid=dt_arcids[expected_idx]: eid in url)
                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(500)

            # Now at archive 3.
            assert f"id={dt_arcids[2]}" in page.url

            # Browser back: should go to archive 2 (previous history entry).
            LOGGER.info("Pressing browser back.")
            responses.clear()
            console_evts.clear()
            await page.go_back(wait_until="networkidle")
            back_url = page.url
            LOGGER.info(f"After first back: {back_url}")
            assert f"id={dt_arcids[1]}" in back_url, f"Expected archive 2 after back, got {back_url}"

            # Browser forward: should return to archive 3.
            LOGGER.info("Pressing browser forward.")
            responses.clear()
            console_evts.clear()
            await page.go_forward(wait_until="networkidle")
            forward_url = page.url
            LOGGER.info(f"After forward: {forward_url}")
            assert f"id={dt_arcids[2]}" in forward_url, f"Expected archive 3 after forward, got {forward_url}"

            # Browser back three times: A3 → A2 → A1 → index.
            LOGGER.info("Pressing browser back three times to reach index.")
            responses.clear()
            console_evts.clear()
            for _ in range(3):
                await page.go_back(wait_until="networkidle")
            index_url = page.url
            LOGGER.info(f"After three backs: {index_url}")
            assert "/reader" not in index_url, f"Expected index after three backs, got {index_url}"

            assert await page.title() == LRR_INDEX_TITLE, "Expected index page title after back"
            await page.locator("#thumbs_container .id1").first.wait_for(state="visible")
            thumb_count = await page.locator("#thumbs_container .id1").count()
            assert thumb_count == 3, f"Expected 3 archives on index after back, got {thumb_count}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("navigation")
async def test_reader_to_index_cross_dt(
        lrr_client: LRRClient, semaphore: asyncio.Semaphore,
        environment: AbstractLRRDeploymentContext,
):
    """
    Test reader to index after cross-DT archive navigation.

    1. Set pagesize=3, upload 5 archives (3 pages each).
    2. Navigate to index, open last archive on DT page 1.
    3. Navigate forward via ArrowRight into DT page 2 archive.
    4. Press browser back, verify return to previous archive.
    5. Click return-to-index icon, verify return to index.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> CONFIG STAGE >>>>>
    environment.set_pagesize(3)
    # <<<<< CONFIG STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(5):
            archive_path = create_archive_file(Path(tmpdir), f"archive-{i+1}", num_pages=3)
            response, error = await upload_archive(
                lrr_client, archive_path, archive_path.name, semaphore,
                title=f"CrossDT {i+1}", tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # Navigate to index page.
            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")

            # Collect DT order (pagesize=3, so first page shows 3 archives).
            search_response_body = None
            for resp in responses:
                if "/search" not in resp.url or resp.request.method != "GET" or resp.status != 200:
                    continue
                body = json.loads(await resp.text())
                if "data" in body and len(body["data"]) == 3:
                    search_response_body = body
                    break
            assert search_response_body is not None, "Did not find datatables search response with 3 archives"
            dt_arcids = [entry["arcid"] for entry in search_response_body["data"]]
            dt_titles = [entry["title"] for entry in search_response_body["data"]]
            LOGGER.info(f"DT page 1 order: {list(zip(dt_titles, dt_arcids))}")
            responses.clear()
            console_evts.clear()

            # Open last archive on DT page 1 (3rd of 3).
            LOGGER.info(f"Opening last archive on DT page 1: {dt_titles[2]}")
            await page.locator("#thumbs_container a", has_text=dt_titles[2]).first.click()
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            assert f"id={dt_arcids[2]}" in page.url

            # Navigate forward through 3 pages to cross into DT page 2.
            LOGGER.info("Navigating forward to cross DT boundary.")
            responses.clear()
            console_evts.clear()
            for i in range(3):
                await page.keyboard.press("ArrowRight")
                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(500)

            # After 3 ArrowRight presses from page 1 of archive 3 (last on DT page 1),
            # we should have crossed into the next archive (first on DT page 2).
            cross_dt_url = page.url
            LOGGER.info(f"After cross-DT navigation: {cross_dt_url}")
            assert f"id={dt_arcids[2]}" not in cross_dt_url, "Expected to have crossed DT boundary"

            # Browser back should go to previous archive (archive 3), not index.
            LOGGER.info("Pressing browser back.")
            responses.clear()
            console_evts.clear()
            await page.go_back(wait_until="networkidle")
            back_url = page.url
            LOGGER.info(f"After back: {back_url}")
            assert f"id={dt_arcids[2]}" in back_url, f"Expected archive 3 after back, got {back_url}"

            # Return-to-index icon should navigate to index.
            LOGGER.info("Clicking return-to-index icon.")
            responses.clear()
            console_evts.clear()
            await page.locator("#return-to-index").click()
            await page.wait_for_load_state("networkidle")
            index_url = page.url
            LOGGER.info(f"After return-to-index: {index_url}")
            assert "/reader" not in index_url, f"Expected index page after icon click, got {index_url}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_forward_history_after_redraw(
        lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Test that forward history is preserved after a datatables redraw.

    1. Upload 1 archive.
    2. Navigate to index with search filter matching the archive.
    3. Click archive to enter reader.
    4. Browser back to index, then reload (triggers non-popstate drawCallback).
    5. Browser forward, verify return to reader.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "archive_1", num_pages=3)
        response, error = await upload_archive(
            lrr_client, archive_path, archive_path.name, semaphore,
            title="History 1", tags="",
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # Navigate to index with search filter.
            await page.goto(f"{lrr_client.lrr_base_url}/?q=History")
            await page.wait_for_load_state("networkidle")
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")

            # Find the archive in DT results.
            search_response_body = None
            for resp in responses:
                if "/search" not in resp.url or resp.request.method != "GET" or resp.status != 200:
                    continue
                body = json.loads(await resp.text())
                if "data" in body and len(body["data"]) == 1:
                    search_response_body = body
                    break
            assert search_response_body is not None, "Did not find search response with 1 archive"
            dt_title = search_response_body["data"][0]["title"]
            responses.clear()
            console_evts.clear()

            # Click archive to enter reader.
            await page.locator("#thumbs_container a", has_text=dt_title).first.click()
            await page.wait_for_load_state("networkidle")
            assert "/reader" in page.url
            LOGGER.debug(f"Entered reader: {page.url}")

            # Browser back to index (popstate-guarded, pushState skipped).
            responses.clear()
            console_evts.clear()
            await page.go_back(wait_until="networkidle")
            LOGGER.debug(f"After back: {page.url}")
            assert "/reader" not in page.url

            # Reload triggers a fresh drawCallback (not popstate-guarded).
            responses.clear()
            console_evts.clear()
            await page.reload(wait_until="networkidle")
            LOGGER.debug(f"After reload: {page.url}")

            # Browser forward should return to reader.
            responses.clear()
            console_evts.clear()
            await page.go_forward(wait_until="networkidle")
            forward_url = page.url
            LOGGER.debug(f"After forward: {forward_url}")
            assert "/reader" in forward_url, f"Forward history was wiped by drawCallback pushState, got {forward_url}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_back_stack_no_growth_on_reload(
        lrr_client: LRRClient,
):
    """
    Test that reloading the index does not add duplicate history entries.

    1. Navigate to login page (anchor).
    2. Navigate to index with search filter.
    3. Reload the page.
    4. Press back twice, verify arrival at login page (no duplicate entries).
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # Navigate to login page as anchor.
            await page.goto(f"{lrr_client.lrr_base_url}/login")
            await page.wait_for_load_state("networkidle")
            responses.clear()
            console_evts.clear()

            # Navigate to index with search filter.
            await page.goto(f"{lrr_client.lrr_base_url}/?q=test")
            await page.wait_for_load_state("networkidle")
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
            responses.clear()
            console_evts.clear()

            # Reload (drawCallback fires with non-popstate pushState).
            await page.reload(wait_until="networkidle")
            LOGGER.debug(f"After reload: {page.url}")
            responses.clear()
            console_evts.clear()

            # Back twice should reach the login anchor.
            # Without fix: reload added a duplicate, so 2 backs only reaches
            # the goto entry (still index).
            # With fix: reload pushState was skipped, so 2 backs reaches login.
            await page.go_back(wait_until="networkidle")
            LOGGER.debug(f"After first back: {page.url}")
            await page.go_back(wait_until="networkidle")
            back_url = page.url
            LOGGER.debug(f"After second back: {back_url}")
            assert "/login" in back_url, f"Back stack has duplicate entries from reload, got {back_url}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<
