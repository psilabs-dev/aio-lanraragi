"""
Index page UI integration tests for the LANraragi server.
Covers behavior of index page, datatables, UI-side search behavior, index pagination, etc.

Compared to search, which tests dedicated search API behavior, search-related tests
in test_index should test the visual behavior of LRR through playwright.
"""

import asyncio
import logging
from pathlib import Path
import tempfile
from typing import AsyncGenerator, Dict, Generator, List

import pytest
import pytest_asyncio
import playwright.async_api
import playwright.async_api._generated

from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.helpers import (
    create_archive_file,
    expect_no_error_logs,
    get_bounded_sem,
    trigger_stat_rebuild,
    upload_archive,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.helpers import assert_browser_responses_ok

LOGGER = logging.getLogger(__name__)

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(
        request, resource_prefix, port_offset, logger=LOGGER
    )
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)

    environments: Dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

    yield environment
    environment.teardown(remove_data=True)

@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield get_bounded_sem()


@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_index_page(lrr_client: LRRClient):
    """
    Test that the index page doesn't throw errors.
    """
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()

        try:
            page = await browser.new_page()

            # capture all network request responses
            responses: List[playwright.async_api._generated.Response] = []
            page.on("response", lambda response: responses.append(response))

            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state('domcontentloaded')
            await page.wait_for_load_state('networkidle')

            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")

            # check browser responses were OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
        finally:
            await browser.close()

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_custom_column_sort_display(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
):
    """
    Regression test to verify that sorting by a custom column displays the correct
    column name in the sort dropdown.

    Issue: PR #1436 incorrectly uses jQuery's .value property instead of .val() method
    when accessing the columnCount select element. This causes the condition to always
    fail, resulting in "title" being displayed instead of the custom column name.

    Test flow:
    1. Upload archives with tags that include custom column namespaces
    2. Navigate to index page
    3. Configure custom columns via localStorage
    4. Click on a custom column header to sort by it
    5. Verify the sort dropdown does NOT show "title" (the bug symptom)
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        num_pages = 5

        archive_specs = [
            {"name": "test_archive_1", "title": "Alpha Archive", "tags": "artist:Alice,series:Test"},
            {"name": "test_archive_2", "title": "Beta Archive", "tags": "artist:Bob,series:Test"},
            {"name": "test_archive_3", "title": "Gamma Archive", "tags": "artist:Charlie,series:Test"},
        ]

        for spec in archive_specs:
            save_path = create_archive_file(tmpdir, spec["name"], num_pages)
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=spec["title"], tags=spec["tags"]
            )
            assert not error, f"Upload failed for {spec['title']} (status {error.status}): {error.error}"
            LOGGER.debug(f"Uploaded {spec['title']} with arcid {response.arcid}")
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> STAT REBUILD STAGE >>>>>
    await trigger_stat_rebuild(lrr_client)
    LOGGER.debug("Stat hash rebuild completed.")
    # <<<<< STAT REBUILD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()

        try:
            page = await browser.new_page()

            # capture all network request responses
            responses: List[playwright.async_api._generated.Response] = []
            page.on("response", lambda response: responses.append(response))

            await page.goto(lrr_client.lrr_base_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")

            # Dismiss any popups (new version release notes)
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            # Wait for DataTable to initialize
            await asyncio.sleep(2)

            # Configure custom columns via localStorage
            await page.evaluate("""() => {
                localStorage.setItem('columnCount', '2');
                localStorage.setItem('customColumn1', 'artist');
                localStorage.setItem('customColumn2', 'series');
            }""")

            # Reload page to apply localStorage settings
            await page.reload()
            await page.wait_for_load_state("domcontentloaded")

            # Dismiss popup again if it appears
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            # Wait for DataTable to reinitialize
            await asyncio.sleep(2)

            # Switch to compact/table mode to see column headers
            compact_toggle = page.locator(".thumbnail-toggle")
            await compact_toggle.click()
            await asyncio.sleep(1)

            # Check that the artist column header exists
            artist_header_link = page.locator("#header-1")
            await artist_header_link.wait_for(state="visible", timeout=5000)

            # The <th> element that DataTables uses for sorting
            artist_th = page.locator("#customheader1")
            sort_dropdown = page.locator("#namespace-sortby")

            # Click on artist column to sort by it
            await artist_th.click()
            await asyncio.sleep(2)

            # Get DataTable order and dropdown value after click
            order_after = await page.evaluate("() => IndexTable.dataTable ? IndexTable.dataTable.order()[0] : null")
            sort_value_after_click = await sort_dropdown.input_value()

            # The bug in PR #1436: `$("#columnCount").value` returns undefined because jQuery
            # objects don't have a .value property. This causes the condition to fail and
            # the dropdown incorrectly shows "title" even when sorting by a custom column.
            #
            # Expected: dropdown does NOT show "title" when sorting by custom column
            # Broken: dropdown shows "title" (the fallback value)
            assert sort_value_after_click != "title", (
                f"Sort dropdown should NOT show 'title' after clicking artist column header. "
                f"Got '{sort_value_after_click}'. "
                f"This indicates the jQuery .value bug where columnCount.value returns undefined."
            )

            # Verify DataTable is actually sorted by the custom column
            assert order_after[0] == 1, (
                f"DataTable should be sorted by column 1 (artist). Got column {order_after[0]}."
            )

            # Verify series column (customColumn2) also works
            series_th = page.locator("#customheader2")
            await series_th.click()
            await asyncio.sleep(2)

            sort_value_series = await sort_dropdown.input_value()
            assert sort_value_series != "title", (
                f"Sort dropdown should NOT show 'title' after clicking series column. "
                f"Got '{sort_value_series}'."
            )

            # Verify title column sorting works correctly
            title_header = page.locator("#titleheader")
            await title_header.click()
            await asyncio.sleep(2)

            sort_value_title = await sort_dropdown.input_value()
            assert sort_value_title == "title", (
                f"Sort dropdown should show 'title' after clicking title column. "
                f"Got '{sort_value_title}'."
            )

            # check browser responses were OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
        finally:
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment)
