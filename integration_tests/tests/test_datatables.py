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
from lanraragi.models.archive import ClearNewArchiveFlagRequest
from lanraragi.models.search import SearchArchiveIndexRequest
from lanraragi.models.tankoubon import (
    AddArchiveToTankoubonRequest,
    CreateTankoubonRequest,
)

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive
from aio_lanraragi_tests.utils.concurrency import get_bounded_sem, retry_on_lock
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
@pytest.mark.dev("navigation")
async def test_navigation_new_category_cross_dt(
        lrr_client: LRRClient, semaphore: asyncio.Semaphore,
        environment: AbstractLRRDeploymentContext,
):
    """
    Cross-DT-page archive navigation must stay within the "New Archives" special
    category (regression for the NEW_ONLY/UNTAGGED_ONLY neighbor-prefetch drift).

    The reader prefetches neighbor pages via /api/search/ids. The built-in "New"
    selector is not a real category ID: the search engine only honors it through the
    newonly flag. If the reader sends category=NEW_ONLY, the server's get_category
    rejects the short id and silently returns the *unfiltered* library, so crossing a
    DT page boundary lands on a non-new archive.

    1. pagesize=3, upload 6 archives (3 pages each); clear the "new" flag on the 4th
       in title order so the New set and the full library diverge at the page-2 boundary:
       - full-library page-2 first -> the non-new archive  [the buggy landing]
       - New-set     page-2 first -> the next new archive  [the correct landing]
    2. Toggle "New Archives", open the last archive on New DT page 1.
    3. Navigate forward across the DT boundary; assert we land on the New set's next
       archive, never the unfiltered library's non-new archive.
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
        for i in range(6):
            archive_path = create_archive_file(Path(tmpdir), f"newcat-{i+1}", num_pages=3)
            response, error = await upload_archive(
                lrr_client, archive_path, archive_path.name, semaphore,
                title=f"NewCat {i+1}", tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> EXPECTED ORDER STAGE >>>>>
    # Full library order (all 6 are new on upload). The page-2 first archive is what the
    # bug surfaces; we will make it the non-new archive so the lineups diverge there.
    response, error = await lrr_client.search_api.search_archive_index(SearchArchiveIndexRequest(start="-1"))
    assert not error, f"Failed to fetch full search order (status {error.status}): {error.error}"
    full_order = [record.arcid for record in response.data]
    titles = {record.arcid: record.title for record in response.data}
    assert len(full_order) == 6, f"Expected 6 archives in full order, got {len(full_order)}"
    non_new_arcid = full_order[3]          # title-order 4th -> DT page 2 (pagesize=3) first
    buggy_landing_arcid = non_new_arcid    # what the unfiltered prefetch would surface

    # Drop the 4th from the New set without touching the full-library order.
    response, error = await retry_on_lock(lambda: lrr_client.archive_api.clear_new_archive_flag(
        ClearNewArchiveFlagRequest(arcid=non_new_arcid)
    ))
    assert not error, f"Failed to clear new flag (status {error.status}): {error.error}"

    # New-set order: this is the lineup cross-boundary navigation must follow.
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", newonly=True)
    )
    assert not error, f"Failed to fetch newonly search order (status {error.status}): {error.error}"
    new_order = [record.arcid for record in response.data]
    assert len(new_order) == 5, f"Expected 5 new archives after clearing one, got {len(new_order)}"
    assert non_new_arcid not in new_order, "Cleared archive still present in the New set"
    page1_last_arcid = new_order[2]            # last on New DT page 1 (pagesize=3)
    expected_landing_arcid = new_order[3]      # first on New DT page 2 -> correct landing
    assert expected_landing_arcid != buggy_landing_arcid, (
        "Test setup invariant: New and full lineups must diverge at the page boundary"
    )
    page1_last_title = titles[page1_last_arcid]
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

            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")

            # Toggle the "New Archives" special category and wait for the DT redraw.
            LOGGER.info("Toggling New Archives category.")
            responses.clear()
            console_evts.clear()
            await page.locator("#NEW_ONLY").click()
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)

            # The New page 1 must contain the boundary archive, and must NOT contain the non-new one.
            new_page1_visible = await page.locator(f"#thumbs_container a[href*='id={page1_last_arcid}']").count()
            assert new_page1_visible > 0, f"Expected New DT page 1 to contain {page1_last_title!r}"
            non_new_visible = await page.locator(f"#thumbs_container a[href*='id={non_new_arcid}']").count()
            assert non_new_visible == 0, "Non-new archive unexpectedly present in the New Archives lineup"

            # Open the last archive on New DT page 1.
            LOGGER.info(f"Opening last archive on New DT page 1: {page1_last_title}")
            await page.locator(f"#thumbs_container a[href*='id={page1_last_arcid}']").first.click()
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            assert f"id={page1_last_arcid}" in page.url

            # Read past the last page (3 pages -> 3 presses) to cross the DT boundary.
            LOGGER.info("Navigating forward across the New DT boundary.")
            responses.clear()
            console_evts.clear()
            for _ in range(3):
                await page.keyboard.press("ArrowRight")
                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(500)

            cross_dt_url = page.url
            LOGGER.info(f"After cross-DT navigation: {cross_dt_url}")
            assert f"id={page1_last_arcid}" not in cross_dt_url, "Expected to have crossed the DT boundary"
            assert f"id={buggy_landing_arcid}" not in cross_dt_url, (
                "Crossed into the unfiltered library: landed on the non-new archive "
                f"{buggy_landing_arcid} (NEW_ONLY neighbor-prefetch drift regression)"
            )
            assert f"id={expected_landing_arcid}" in cross_dt_url, (
                f"Expected to land on the New set's next archive {expected_landing_arcid}, got {cross_dt_url}"
            )

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


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("navigation-sort")
async def test_navigation_sort_by_namespace(
        lrr_client: LRRClient, semaphore: asyncio.Semaphore,
        environment: AbstractLRRDeploymentContext,
):
    """
    Test that a `?sort=<namespace>` index URL sorts by that namespace and drives
    archive navigation in that order, rather than by title.

    1. Upload 4 archives tagged with a shared series and chapter:NNN values
       assigned so chapter order diverges from title order.
    2. With `chapter` bound to a display column (the name-based sort resolves a namespace
       to an already-bound column, not an arbitrary one), open the index at
       /?q=series:...$&sort=chapter; verify the URL stays in the namespace form
       (sort=chapter, not a column index) on round-trip.
    3. Enter the reader on the first archive and step forward with "." across the
       whole lineup, verifying each landing matches the chapter-sorted order.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    # Titles and chapters are deliberately misaligned so a chapter sort differs from a title sort.
    title_to_chapter = {"Delta": 1, "Alpha": 2, "Charlie": 3, "Bravo": 4}
    title_to_arcid: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        for title, chapter in title_to_chapter.items():
            archive_path = create_archive_file(Path(tmpdir), f"archive-{chapter}", num_pages=3)
            response, error = await upload_archive(
                lrr_client, archive_path, archive_path.name, semaphore,
                title=title, tags=f"series:Manga, chapter:{str(chapter).zfill(3)}",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            title_to_arcid[title] = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> GROUND TRUTH STAGE >>>>>
    # Derive chapter-sorted and title-sorted orderings from the search API; they must differ
    # for the test to distinguish a real chapter sort from a title fallback.
    chapter_order: list[str] = []
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="series:Manga$", sortby="chapter", order="asc", start="-1")
    )
    assert not error, f"Chapter-sorted search failed (status {error.status}): {error.error}"
    for record in response.data:
        chapter_order.append(record.arcid)

    title_order: list[str] = []
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="series:Manga$", sortby="title", order="asc", start="-1")
    )
    assert not error, f"Title-sorted search failed (status {error.status}): {error.error}"
    for record in response.data:
        title_order.append(record.arcid)

    assert len(chapter_order) == 4, f"Expected 4 archives in chapter order, got {len(chapter_order)}"
    assert chapter_order != title_order, "Chapter and title orderings must differ to detect the sort"
    arcid_to_title = {arcid: title for title, arcid in title_to_arcid.items()}
    LOGGER.info(f"Chapter order: {[arcid_to_title[a] for a in chapter_order]}")
    del response, error
    # <<<<< GROUND TRUTH STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()
        # Bind a display column to the `chapter` namespace before the index loads. The
        # name-based ?sort= reader resolves a namespace to a column already bound to it; it
        # does not auto-bind an arbitrary namespace to a column. Seeding customColumn1 is how a
        # real user who wants to sort by chapter has it configured.
        await bc.add_init_script("localStorage.setItem('customColumn1', 'chapter');")

        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page_errors: list[str] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            # Open the index already sorted by the chapter namespace via the URL.
            await page.goto(f"{lrr_client.lrr_base_url}/?q=series%3AManga%24&sort=chapter")
            await page.wait_for_load_state("networkidle")
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)

            # The URL must round-trip as the namespace name, not a column index.
            assert "sort=chapter" in page.url, f"Expected namespace sort to persist in URL, got {page.url}"
            assert "sort=1" not in page.url, f"URL should not collapse the namespace to a column index, got {page.url}"

            # The index page must be JS-error-free. A throwing drawCallback (e.g. a bad
            # column accessor in buildURLParameters) is an *uncaught* exception, not a
            # console.error, so it surfaces via pageerror rather than console_evts; it
            # leaves the spinner stuck and silently breaks the URL writer. Assert here,
            # before the reader stage clears the console.
            assert not page_errors, f"Uncaught JS error(s) on the index page: {page_errors}"
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)

            # Writer isolation: the round-trip check above is partly satisfied by the URL we
            # navigated to, so it doesn't prove the app *generated* the name. Toggle the sort
            # direction to force buildURLParameters to regenerate the URL from scratch: the new
            # sortdir=desc is the witness that the writer ran, and sort=chapter (not sort=1)
            # proves it emitted the namespace name rather than the column index.
            await page.click("#order-sortby")
            # buildURLParameters rewrites the URL via pushState inside the (async) DataTables draw
            # callback, which can land after networkidle -- wait on the URL itself, not the network.
            # (assert_no_spinner watches the reader's #i3 spinner, which never exists on the index.)
            await page.wait_for_function("() => location.search.includes('sortdir=desc')", timeout=5000)
            assert "sortdir=desc" in page.url, f"Sort-direction toggle must regenerate the URL, got {page.url}"
            assert "sort=chapter" in page.url, f"Writer must regenerate the namespace name, got {page.url}"
            assert "sort=1" not in page.url, f"Writer must not regenerate a column index, got {page.url}"
            # Restore ascending order for the reader-navigation stage below.
            await page.click("#order-sortby")
            await page.wait_for_function("() => !location.search.includes('sortdir=desc')", timeout=5000)
            assert "sortdir=desc" not in page.url, f"Expected ascending order restored, got {page.url}"

            # Enter the reader on the first chapter-sorted archive.
            first_title = arcid_to_title[chapter_order[0]]
            LOGGER.info(f"Opening first chapter-sorted archive: {first_title}")
            responses.clear()
            console_evts.clear()
            await page.locator("#thumbs_container a", has_text=first_title).first.click()
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            assert f"id={chapter_order[0]}" in page.url, f"Expected first chapter archive in URL, got {page.url}"

            # Step forward with "." across the lineup; each landing must follow chapter order.
            for expected_arcid in chapter_order[1:]:
                await page.keyboard.press(".")
                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(500)
                assert f"id={expected_arcid}" in page.url, (
                    f"Expected next archive {arcid_to_title[expected_arcid]} in chapter order, got {page.url}"
                )

            assert not page_errors, f"Uncaught JS error(s) during reader navigation: {page_errors}"
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<
