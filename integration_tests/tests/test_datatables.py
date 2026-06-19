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
from lanraragi.models.search import SearchArchiveIndexRequest
from lanraragi.models.tankoubon import (
    AddArchiveToTankoubonRequest,
    CreateTankoubonRequest,
)

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
async def test_reader_to_index_cross_dt(
        lrr_client: LRRClient, semaphore: asyncio.Semaphore,
        environment: AbstractLRRDeploymentContext,
):
    """
    Test reader to index after cross-DT archive navigation.

    1. Set pagesize=3, upload 5 archives (3 pages each).
    2. Navigate to index, open last archive on DT page 1.
    3. Navigate forward via ArrowRight into DT page 2 archive.
    4. Click return-to-index icon, verify return to index.
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

    # >>>>> EXPECTED ORDER STAGE >>>>>
    # The cross-DT hop is driven by /api/search/ids, which delivers DT page 2's lineup.
    # Capture the full search order so we can assert the exact landing archive: DT page 2's
    # first archive is the 4th in the order (pagesize=3).
    response, error = await lrr_client.search_api.search_archive_index(SearchArchiveIndexRequest(start="-1"))
    assert not error, f"Failed to fetch full search order (status {error.status}): {error.error}"
    full_order = []
    for record in response.data:
        full_order.append(record.arcid)
    assert len(full_order) == 5, f"Expected 5 archives in search order, got {len(full_order)}"
    expected_next_arcid = full_order[3]
    del response, error
    # <<<<< EXPECTED ORDER STAGE <<<<<

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
            assert dt_arcids[2] == full_order[2], "Index DT order disagrees with search API order at the page boundary"
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
            assert f"id={expected_next_arcid}" in cross_dt_url, (
                f"Expected to land on DT page 2's first archive {expected_next_arcid}, got {cross_dt_url}"
            )

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


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("navigation")
async def test_navigation_grouptanks(
        lrr_client: LRRClient, semaphore: asyncio.Semaphore,
        environment: AbstractLRRDeploymentContext,
):
    """
    Test that cross-DT navigation preserves the ungrouped (grouptanks=false) lineup.

    1. Set pagesize=3, upload 6 archives (3 pages each), group archives 1 and 2 into a tank
       so the grouped and ungrouped orderings diverge.
    2. Ungrouped mode (grouptanks=false): open the last archive on DT page 1, navigate forward
       across the DT boundary, and verify the landing is the ungrouped page-2 neighbor rather
       than a grouped neighbor or a tank.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> CONFIG STAGE >>>>>
    environment.set_pagesize(3)
    # <<<<< CONFIG STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    title_to_arcid: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(6):
            title = f"Archive {i + 1}"
            archive_path = create_archive_file(Path(tmpdir), f"archive-{i + 1}", num_pages=3)
            response, error = await upload_archive(
                lrr_client, archive_path, archive_path.name, semaphore,
                title=title, tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            title_to_arcid[title] = response.arcid
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> TANKOUBON STAGE >>>>>
    # Group archives 1 and 2 into a tank so the grouped and ungrouped orderings diverge.
    response, error = await lrr_client.tankoubon_api.create_tankoubon(CreateTankoubonRequest(name="Archive 0 Collection"))
    assert not error, f"Failed to create tankoubon (status {error.status}): {error.error}"
    tank_id = response.tank_id
    for title in ["Archive 1", "Archive 2"]:
        response, error = await lrr_client.tankoubon_api.add_archive_to_tankoubon(
            AddArchiveToTankoubonRequest(tank_id=tank_id, arcid=title_to_arcid[title])
        )
        assert not error, f"Failed to add {title} to tankoubon (status {error.status}): {error.error}"
    # <<<<< TANKOUBON STAGE <<<<<

    # >>>>> GROUND TRUTH STAGE >>>>>
    # Derive expected orderings for grouped and ungrouped modes from the search API.
    grouped_page1: list[str] = []
    response, error = await lrr_client.search_api.search_archive_index(SearchArchiveIndexRequest(start="0", sortby="title", order="asc", groupby_tanks=True))
    assert not error, f"Grouped search failed (status {error.status}): {error.error}"
    for record in response.data:
        grouped_page1.append(record.arcid)

    grouped_page2: list[str] = []
    response, error = await lrr_client.search_api.search_archive_index(SearchArchiveIndexRequest(start="3", sortby="title", order="asc", groupby_tanks=True))
    assert not error, f"Grouped search failed (status {error.status}): {error.error}"
    for record in response.data:
        grouped_page2.append(record.arcid)

    ungrouped_page1: list[str] = []
    response, error = await lrr_client.search_api.search_archive_index(SearchArchiveIndexRequest(start="0", sortby="title", order="asc", groupby_tanks=False))
    assert not error, f"Ungrouped search failed (status {error.status}): {error.error}"
    for record in response.data:
        ungrouped_page1.append(record.arcid)

    ungrouped_page2: list[str] = []
    response, error = await lrr_client.search_api.search_archive_index(SearchArchiveIndexRequest(start="3", sortby="title", order="asc", groupby_tanks=False))
    assert not error, f"Ungrouped search failed (status {error.status}): {error.error}"
    for record in response.data:
        ungrouped_page2.append(record.arcid)

    LOGGER.debug(f"Grouped page1: {grouped_page1}, page2: {grouped_page2}")
    LOGGER.debug(f"Ungrouped page1: {ungrouped_page1}, page2: {ungrouped_page2}")

    # Preconditions that make this test meaningful.
    grouped_all = grouped_page1 + grouped_page2
    assert tank_id in grouped_all, "Tank should appear as a row in grouped results"
    assert tank_id not in ungrouped_page1 + ungrouped_page2, "Tank should not appear in ungrouped results"
    assert ungrouped_page2[0] != grouped_page2[0], "Grouped and ungrouped page-2 neighbors must differ to detect the bug"
    # <<<<< GROUND TRUTH STAGE <<<<<

    # >>>>> UI STAGE: UNGROUPED MODE >>>>>
    last_arc_page1 = ungrouped_page1[-1]
    expected_neighbor = ungrouped_page2[0]
    last_title = None
    for title, arcid in title_to_arcid.items():
        if arcid == last_arc_page1:
            last_title = title
            break
    assert last_title is not None, "Could not resolve title for last archive on ungrouped page 1"

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        await bc.add_init_script("localStorage.setItem('grouptanks', 'false');")
        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")

            LOGGER.debug(f"Opening last archive on ungrouped DT page 1: {last_title}")
            await page.locator("#thumbs_container a", has_text=last_title).first.click()
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            assert f"id={last_arc_page1}" in page.url, f"Expected last page-1 archive in URL, got {page.url}"

            # Navigate forward across the DT page boundary (3 pages per archive).
            responses.clear()
            console_evts.clear()
            LOGGER.debug("Navigating forward across the ungrouped DT boundary.")
            for _ in range(3):
                await page.keyboard.press("ArrowRight")
                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(500)

            assert f"id={expected_neighbor}" in page.url, f"Expected ungrouped neighbor {expected_neighbor} after crossing DT boundary, got {page.url}"
            assert "TANK_" not in page.url, f"Ungrouped navigation should not land on a tank, got {page.url}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE: UNGROUPED MODE <<<<<
