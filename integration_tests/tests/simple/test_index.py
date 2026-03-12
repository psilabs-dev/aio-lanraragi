"""
Index page UI integration tests for the LANraragi server.
Covers behavior of index page, datatables, custom column sorting, etc.
"""

import asyncio
import json
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

import playwright.async_api
import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.common import DEFAULT_LRR_PASSWORD
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    trigger_stat_rebuild,
    upload_archive,
)
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.xfail(reason="PR: https://github.com/Difegue/LANraragi/pull/1468")
async def test_header_click_sort(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Test that clicking a column header in compact mode triggers DataTables sort.

    1. Upload 3 archives with distinct titles, rebuild stat hash.
    2. Open index page, switch to compact/table mode.
    3. Click the Title column header (default is already asc, so first click toggles to desc).
       - Expect the header gains sorting_desc class.
       - Capture the search response triggered by the header click.
       - Expect archives sorted by title descending.
    4. Click the Title header again.
       - Expect the header gains sorting_asc class.
       - Capture the search response.
       - Expect archives sorted by title ascending.
    5. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        for i in range(3):
            save_path = create_archive_file(tmpdir, f"test-archive-{i}", 3)
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=f"archive {chr(ord('C') - i)}", tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> STAT REBUILD STAGE >>>>>
    await trigger_stat_rebuild(lrr_client)
    # <<<<< STAT REBUILD STAGE <<<<<

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

            await page.goto(lrr_client.lrr_base_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            # dismiss new version overlay if present
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            # switch to compact/table mode
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            compact_toggle = page.locator(".thumbnail-toggle")
            await compact_toggle.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)

            # drag-resize the title header to trigger resizableColumns mousedown
            title_header = page.locator("th.title")
            box = await title_header.bounding_box()
            right_edge_x = box["x"] + box["width"] - 5
            center_y = box["y"] + box["height"] / 2
            await page.mouse.move(right_edge_x, center_y)
            await page.mouse.down()
            await page.mouse.move(right_edge_x + 20, center_y)
            await page.mouse.up()
            await page.wait_for_timeout(200)

            # click title header to sort descending (default is already asc)
            search_future: asyncio.Future = asyncio.get_event_loop().create_future()
            async def on_desc_response(response: playwright.async_api._generated.Response) -> None:
                if search_future.done():
                    return
                if "/search" not in response.url or response.request.method != "GET" or response.status != 200:
                    return
                body = json.loads(await response.text())
                if "data" in body and len(body["data"]) == 3:
                    search_future.set_result(body)
            page.on("response", on_desc_response)

            await title_header.click()
            search_response_body = await asyncio.wait_for(search_future, timeout=10)
            page.remove_listener("response", on_desc_response)
            await page.wait_for_load_state("networkidle")

            # verify header has sorting_desc class
            header_class = await title_header.get_attribute("class") or ""
            assert "sorting_desc" in header_class, (
                f"Expected sorting_desc on title header after click, got class={header_class!r}"
            )

            # verify title descending order: C, B, A
            sorted_titles = []
            for entry in search_response_body["data"]:
                sorted_titles.append(entry["title"])
            assert sorted_titles == ["archive C", "archive B", "archive A"], (
                f"Expected descending title sort, got: {sorted_titles}"
            )

            # click title header again to sort ascending
            responses.clear()
            console_evts.clear()

            search_future = asyncio.get_event_loop().create_future()
            async def on_asc_response(response: playwright.async_api._generated.Response) -> None:
                if search_future.done():
                    return
                if "/search" not in response.url or response.request.method != "GET" or response.status != 200:
                    return
                body = json.loads(await response.text())
                if "data" in body and len(body["data"]) == 3:
                    search_future.set_result(body)
            page.on("response", on_asc_response)

            await title_header.click()
            search_response_body = await asyncio.wait_for(search_future, timeout=10)
            page.remove_listener("response", on_asc_response)
            await page.wait_for_load_state("networkidle")

            # verify header has sorting_asc class
            header_class = await title_header.get_attribute("class") or ""
            assert "sorting_asc" in header_class, (
                f"Expected sorting_asc on title header after second click, got class={header_class!r}"
            )

            # verify title ascending order: A, B, C
            sorted_titles = []
            for entry in search_response_body["data"]:
                sorted_titles.append(entry["title"])
            assert sorted_titles == ["archive A", "archive B", "archive C"], (
                f"Expected ascending title sort, got: {sorted_titles}"
            )

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.xfail(reason="PR: https://github.com/Difegue/LANraragi/pull/1468")
async def test_compact_column_sort_with_three_columns(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Test that compact mode column sorting works correctly with 2 and 3 columns.

    1. Upload 5 archives with distinct artist/series/group tags, rebuild stat hash.
    2. Switch to compact mode (default 2 columns: artist, series).
    3. Click column 1 (artist) header: assert asc then desc sort order.
    4. Click column 2 (series) header: assert asc then desc sort order.
    5. Change column count to 3 (triggers page reload).
    6. After reload, verify columns 1 and 2 still sort correctly.
    7. Edit column 3 to "Group" (capital G) — assert cells empty (invalid namespace).
    8. Edit column 3 to "group" (lowercase) — assert cells populated (valid namespace).
    9. Click column 3 (group) header: assert desc then asc sort order.
    10. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> ARCHIVE DEFINITION >>>>>
    # Tag values chosen so ascending order differs per namespace.
    archive_specs = [
        {"name": "archive_1", "title": "Title 1", "tags": "artist:c,series:e,group:b", "pages": 3},
        {"name": "archive_2", "title": "Title 2", "tags": "artist:e,series:a,group:d", "pages": 3},
        {"name": "archive_3", "title": "Title 3", "tags": "artist:a,series:d,group:c", "pages": 3},
        {"name": "archive_4", "title": "Title 4", "tags": "artist:d,series:b,group:e", "pages": 3},
        {"name": "archive_5", "title": "Title 5", "tags": "artist:b,series:c,group:a", "pages": 3},
    ]
    num_archives = len(archive_specs)
    expected_asc = {
        "artist": ["Title 3", "Title 5", "Title 1", "Title 4", "Title 2"],
        "series": ["Title 2", "Title 4", "Title 5", "Title 3", "Title 1"],
        "group":  ["Title 5", "Title 1", "Title 3", "Title 2", "Title 4"],
    }
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        for spec in archive_specs:
            save_path = create_archive_file(tmpdir, spec["name"], spec["pages"])
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=spec["title"], tags=spec["tags"],
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # >>>>> STAT REBUILD STAGE >>>>>
    await trigger_stat_rebuild(lrr_client)
    # <<<<< STAT REBUILD STAGE <<<<<

    async def capture_search_response(page: playwright.async_api._generated.Page, num_expected: int) -> tuple[asyncio.Future, Callable]:
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        async def on_response(response: playwright.async_api._generated.Response) -> None:
            if future.done():
                return
            if "/search" not in response.url or response.request.method != "GET" or response.status != 200:
                return
            body = json.loads(await response.text())
            if "data" in body and len(body["data"]) == num_expected:
                future.set_result(body)
        page.on("response", on_response)
        return future, on_response

    async def assert_header_sort(page: playwright.async_api._generated.Page, header_locator: playwright.async_api._generated.Locator, expected_titles: list[str], expected_direction: str) -> None:
        future, listener = await capture_search_response(page, num_archives)
        await header_locator.click()
        body = await asyncio.wait_for(future, timeout=10)
        page.remove_listener("response", listener)
        await page.wait_for_load_state("networkidle")

        header_class = await header_locator.get_attribute("class") or ""
        assert f"sorting_{expected_direction}" in header_class, (
            f"Expected sorting_{expected_direction} on header, got class={header_class!r}"
        )

        titles = [entry["title"] for entry in body["data"]]
        assert titles == expected_titles, (
            f"Expected {expected_direction} order {expected_titles}, got {titles}"
        )

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

            await page.goto(lrr_client.lrr_base_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            # switch to compact mode
            compact_toggle = page.locator(".thumbnail-toggle")
            await compact_toggle.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)

            # >>>>> 2 COLUMNS (DEFAULT: ARTIST, SERIES) >>>>>
            LOGGER.debug("Testing sort with default 2 columns.")

            artist_header = page.locator("#customheader1")
            await artist_header.wait_for(state="visible", timeout=5000)
            series_header = page.locator("#customheader2")
            await series_header.wait_for(state="visible", timeout=5000)

            asc_titles = expected_asc["artist"]
            desc_titles = list(reversed(asc_titles))
            await assert_header_sort(page, artist_header, asc_titles, "asc")
            await assert_header_sort(page, artist_header, desc_titles, "desc")

            asc_titles = expected_asc["series"]
            desc_titles = list(reversed(asc_titles))
            await assert_header_sort(page, series_header, asc_titles, "asc")
            await assert_header_sort(page, series_header, desc_titles, "desc")

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()
            # <<<<< 2 COLUMNS (DEFAULT: ARTIST, SERIES) <<<<<

            # >>>>> 3 COLUMNS (CHANGE COLUMN COUNT) >>>>>
            LOGGER.debug("Changing column count to 3.")

            column_count_select = page.locator("#columnCount")
            async with page.expect_navigation(timeout=30000):
                await column_count_select.select_option("3")
            await page.wait_for_load_state("networkidle")

            # dismiss overlay if present after reload
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            await page.wait_for_timeout(500)

            artist_header = page.locator("#customheader1")
            await artist_header.wait_for(state="visible", timeout=5000)
            series_header = page.locator("#customheader2")
            await series_header.wait_for(state="visible", timeout=5000)
            header3 = page.locator("#customheader3")
            await header3.wait_for(state="visible", timeout=5000)

            # verify columns 1 and 2 still sort correctly after column count change
            asc_titles = expected_asc["artist"]
            desc_titles = list(reversed(asc_titles))
            await assert_header_sort(page, artist_header, asc_titles, "asc")
            await assert_header_sort(page, artist_header, desc_titles, "desc")

            asc_titles = expected_asc["series"]
            desc_titles = list(reversed(asc_titles))
            await assert_header_sort(page, series_header, asc_titles, "asc")
            await assert_header_sort(page, series_header, desc_titles, "desc")

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()
            # <<<<< 3 COLUMNS (CHANGE COLUMN COUNT) <<<<<

            # >>>>> EDIT COLUMN 3 NAMESPACE >>>>>
            # Phase 3a: "Group" (capital G) — invalid namespace, cells must be empty
            LOGGER.info("Phase 3a: editing column 3 to 'Group' (invalid namespace)")

            edit_btn = page.locator("#edit-header-3")
            await edit_btn.click()

            swal_input = page.locator(".swal2-input")
            await swal_input.wait_for(state="visible", timeout=5000)
            await swal_input.fill("Group")
            swal_confirm = page.locator(".swal2-confirm")
            await swal_confirm.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)

            # "Group" does not match "group:" tags — column 3 cells must be empty
            col3_cells = page.locator("td.customheader3")
            cell_count = await col3_cells.count()
            assert cell_count == num_archives, (
                f"Expected {num_archives} rows, got {cell_count}"
            )
            for i in range(cell_count):
                text = (await col3_cells.nth(i).inner_text()).strip()
                assert text == "", (
                    f"Expected empty cell for invalid namespace 'Group', got: '{text}'"
                )

            # Phase 3b: "group" (lowercase) — valid namespace, cells populated + sort works
            LOGGER.info("Phase 3b: editing column 3 to 'group' (valid namespace)")

            edit_btn = page.locator("#edit-header-3")
            await edit_btn.click()

            swal_input = page.locator(".swal2-input")
            await swal_input.wait_for(state="visible", timeout=5000)
            await swal_input.fill("group")
            swal_confirm = page.locator(".swal2-confirm")
            await swal_confirm.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)

            # "group" matches "group:" tags — column 3 cells must be populated
            col3_cells = page.locator("td.customheader3")
            for i in range(await col3_cells.count()):
                text = (await col3_cells.nth(i).inner_text()).strip()
                assert text != "", (
                    f"Expected non-empty cell for valid namespace 'group', got empty at row {i}"
                )

            # Verify header click sort works correctly with valid namespace
            header3 = page.locator("#customheader3")
            await header3.wait_for(state="visible", timeout=5000)

            asc_titles = expected_asc["group"]
            desc_titles = list(reversed(asc_titles))
            await assert_header_sort(page, header3, asc_titles, "asc")
            await assert_header_sort(page, header3, desc_titles, "desc")

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            # <<<<< EDIT COLUMN 3 NAMESPACE <<<<<
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_index_page(lrr_client: LRRClient) -> None:
    """
    Test that the index page loads without errors.

    1. Navigate to index page.
    2. Expect no HTTP errors and no console errors.
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

            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            # dismiss new version overlay if present
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_custom_column_sort_display(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Test sort dropdown population and custom column sort behavior.

    1. Upload 3 archives with distinct artist/series tags, rebuild stat hash.
    2. Open index page, wait for stat-driven namespace options to populate the
       sort dropdown. Expect "title", "artist", "series" present.
    3. Select "artist" from dropdown, capture the search response.
       - Expect dropdown retains "artist" after DataTables re-draw.
       - Expect archives sorted by artist namespace (artist:Bob last in asc).
    4. Switch to compact/table mode.
       - Expect custom column headers (#customheader1, #customheader2) visible.
    5. Switch back to thumbnail mode, select "series" from dropdown.
       - Expect dropdown retains "series" after DataTables re-draw.
       - Expect archives sorted by series namespace (series:Another first in asc).
    6. Select "title" from dropdown. Expect dropdown retains "title".
    7. Expect no HTTP errors, no console errors, no server error logs.
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

        tags_list = [
            "artist:Alice,series:Test",
            "artist:Alice,series:Test",
            "artist:Bob,series:Another",
        ]

        for i, tags in enumerate(tags_list):
            save_path = create_archive_file(tmpdir, f"test-archive-{i}", num_pages)
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=f"test archive {i}", tags=tags,
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            LOGGER.debug(f"Uploaded archive {i} with arcid {response.arcid}")
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> STAT REBUILD STAGE >>>>>
    await trigger_stat_rebuild(lrr_client)
    LOGGER.debug("Stat hash rebuild completed.")
    # <<<<< STAT REBUILD STAGE <<<<<

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

            await page.goto(lrr_client.lrr_base_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            # dismiss new version overlay if present
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            # wait for stat-driven namespace options to populate the sort dropdown
            sort_dropdown = page.locator("#namespace-sortby")
            await sort_dropdown.locator("option[value='artist']").wait_for(state="attached", timeout=10000)

            # verify dropdown was populated with namespaces from stat hash
            options = await sort_dropdown.locator("option").all()
            option_values = []
            for opt in options:
                option_values.append(await opt.get_attribute("value"))
            assert "title" in option_values, "Expected 'title' in sort dropdown options"
            assert "artist" in option_values, "Expected 'artist' in sort dropdown options (from stat hash)"
            assert "series" in option_values, "Expected 'series' in sort dropdown options (from stat hash)"

            # select artist namespace from dropdown and verify sort order
            responses.clear()
            console_evts.clear()

            # set up a future to capture the search response triggered by the sort change
            search_future: asyncio.Future = asyncio.get_event_loop().create_future()
            async def on_search_response(response: playwright.async_api._generated.Response) -> None:
                if search_future.done():
                    return
                if "/search" not in response.url or response.request.method != "GET" or response.status != 200:
                    return
                body = json.loads(await response.text())
                if "data" in body:
                    search_future.set_result(body)
            page.on("response", on_search_response)

            await sort_dropdown.select_option("artist")
            search_response_body = await asyncio.wait_for(search_future, timeout=10)
            page.remove_listener("response", on_search_response)
            await page.wait_for_load_state("networkidle")

            sort_value = await sort_dropdown.input_value()
            assert sort_value == "artist", (
                f"Sort dropdown should show 'artist' after selecting it. Got '{sort_value}'."
            )

            # verify the search response reflects artist-sorted order
            sorted_titles = []
            for entry in search_response_body["data"]:
                sorted_titles.append(entry["title"])
            assert len(sorted_titles) == 3, f"Expected 3 archives in search response, got {len(sorted_titles)}"
            # archives 0,1 have artist:Alice, archive 2 has artist:Bob; asc order => Alice first
            assert sorted_titles[-1] == "test archive 2", (
                f"Expected 'test archive 2' (artist:Bob) last in ascending artist sort, got order: {sorted_titles}"
            )

            # switch to compact/table mode and verify custom column headers are visible
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            compact_toggle = page.locator(".thumbnail-toggle")
            await compact_toggle.click()
            await page.wait_for_load_state("networkidle")

            artist_header = page.locator("#customheader1")
            await artist_header.wait_for(state="visible", timeout=5000)
            series_header = page.locator("#customheader2")
            await series_header.wait_for(state="visible", timeout=5000)

            # switch back to thumbnail mode and select series sort
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            thumbnail_toggle = page.locator(".compact-toggle")
            await thumbnail_toggle.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)

            # select series from dropdown and verify sort order
            search_future = asyncio.get_event_loop().create_future()
            async def on_series_search_response(response: playwright.async_api._generated.Response) -> None:
                if search_future.done():
                    return
                if "/search" not in response.url or response.request.method != "GET" or response.status != 200:
                    return
                body = json.loads(await response.text())
                if "data" in body and len(body["data"]) == 3:
                    search_future.set_result(body)
            page.on("response", on_series_search_response)

            await sort_dropdown.select_option("series")
            search_response_body = await asyncio.wait_for(search_future, timeout=10)
            page.remove_listener("response", on_series_search_response)
            await page.wait_for_load_state("networkidle")

            sort_value = await sort_dropdown.input_value()
            assert sort_value == "series", (
                f"Sort dropdown should show 'series' after selecting it. Got '{sort_value}'."
            )

            # verify the search response reflects series-sorted order
            sorted_titles = []
            for entry in search_response_body["data"]:
                sorted_titles.append(entry["title"])
            assert len(sorted_titles) == 3, f"Expected 3 archives in search response, got {len(sorted_titles)}"
            # archive 2 has series:Another, archives 0,1 have series:Test; asc => Another first
            assert sorted_titles[0] == "test archive 2", (
                f"Expected 'test archive 2' (series:Another) first in ascending series sort, got order: {sorted_titles}"
            )

            # switch back to title sort
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            await sort_dropdown.select_option("title")
            await page.wait_for_load_state("networkidle")

            sort_value = await sort_dropdown.input_value()
            assert sort_value == "title", (
                f"Sort dropdown should show 'title' after selecting it. Got '{sort_value}'."
            )

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("namespace-exclusion")
async def test_search_autocomplete_namespace_exclusion(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Test that excluded namespaces are filtered from search autocomplete suggestions.

    1. Upload 3 archives with both normal (artist, series) and noisy (source, date_added) tags.
    2. Rebuild stat hashes.
    3. Navigate to settings, configure excluded namespaces via UI, save.
    4. Navigate to index page, verify excluded namespaces absent from sort dropdown.
    5. Type partial match for an excluded tag, verify no suggestions appear.
    6. Type partial match for a non-excluded tag, verify exact expected suggestions.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        archive_tags = [
            "artist:alice,series:foo,source:https://a.test,date_added:1700000000",
            "artist:alice,series:bar,source:https://b.test,date_added:1700000001",
            "artist:bob,series:foo,source:https://c.test,date_added:1700000002",
        ]
        for i, tags in enumerate(archive_tags):
            save_path = create_archive_file(tmpdir, f"archive-{i}", 3)
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=f"archive {i}", tags=tags,
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> STAT REBUILD STAGE >>>>>
    await trigger_stat_rebuild(lrr_client)
    # <<<<< STAT REBUILD STAGE <<<<<

    # >>>>> SETTINGS STAGE >>>>>
    # Navigate to settings UI, configure excluded namespaces, save.
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await bc.new_page()

            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # login to access settings
            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.wait_for_load_state("networkidle")
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")

            # navigate to settings
            await page.goto(f"{lrr_client.lrr_base_url}/config", timeout=60000)
            await page.wait_for_load_state("networkidle")

            # open "Tags and Thumbnails" section
            await page.get_by_text("Tags and Thumbnails").click()
            await page.wait_for_timeout(300)

            # fill in excluded namespaces
            excluded_input = page.locator("input[name='excludednamespaces']")
            await excluded_input.wait_for(state="visible", timeout=5000)
            await excluded_input.fill("source,date_added")

            # save settings
            await page.get_by_role("button", name="Save Settings").click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< SETTINGS STAGE <<<<<

    # verify /api/info returns the excluded namespaces
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {error.status}): {error.error}"

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

            await page.goto(lrr_client.lrr_base_url, timeout=60000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_load_state("networkidle")

            # dismiss new version overlay if present
            if "New Version Release Notes" in await page.content():
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)

            # wait for stat-driven namespace options to populate the sort dropdown
            sort_dropdown = page.locator("#namespace-sortby")
            await sort_dropdown.locator("option[value='artist']").wait_for(state="attached", timeout=10000)

            # verify excluded namespaces are NOT in the sort dropdown
            options = await sort_dropdown.locator("option").all()
            option_values = []
            for opt in options:
                option_values.append(await opt.get_attribute("value"))
            LOGGER.info(f"Sort dropdown options: {option_values}")
            assert "source" not in option_values, "Expected 'source' to be excluded from sort dropdown"
            assert "date_added" not in option_values, "Expected 'date_added' to be excluded from sort dropdown"
            assert "artist" in option_values, "Expected 'artist' in sort dropdown options"
            assert "series" in option_values, "Expected 'series' in sort dropdown options"

            # type a partial match for an excluded namespace tag into search bar
            search_input = page.locator("#search-input")
            await search_input.click()
            await search_input.fill(".test")
            await page.wait_for_timeout(500)

            # excluded tags must not appear in suggestions
            suggestion_items = page.locator(".awesomplete > ul > li")
            suggestions = []
            for i in range(await suggestion_items.count()):
                suggestions.append(await suggestion_items.nth(i).inner_text())
            LOGGER.info(f"Awesomplete suggestions for '.test': {suggestions}")
            assert len(suggestions) == 0, f"Expected no suggestions for excluded namespace, got: {suggestions}"

            # type a partial match for a non-excluded tag
            await search_input.fill("")
            await page.wait_for_timeout(200)
            await search_input.fill("alice")
            await page.wait_for_timeout(500)

            suggestion_items = page.locator(".awesomplete > ul > li")
            suggestions = []
            for i in range(await suggestion_items.count()):
                suggestions.append(await suggestion_items.nth(i).inner_text())
            LOGGER.info(f"Awesomplete suggestions for 'alice': {suggestions}")
            assert len(suggestions) == 1, f"Expected exactly 1 suggestion, got: {suggestions}"
            assert suggestions[0] == "artist:alice", f"Expected 'artist:alice', got: {suggestions[0]}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)
