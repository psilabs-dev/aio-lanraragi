"""
Index page UI integration tests for the LANraragi server.
Covers behavior of index page, datatables, custom column sorting, etc.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import GetArchiveMetadataRequest

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
    PlaywrightTestContextManager,
    assert_no_spinner,
    read_rendered_titles,
    switch_display_mode,
)

LOGGER = logging.getLogger(__name__)

NEW_MARKER = "\U0001f195"  # 🆕 symbol


@pytest.mark.asyncio
@pytest.mark.playwright
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
       - Expect the rendered archives sorted by title descending.
    4. Click the Title header again.
       - Expect the header gains sorting_asc class.
       - Expect the rendered archives sorted by title ascending.
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
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

        await page.goto(lrr_client.lrr_base_url, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_load_state("networkidle")

        # dismiss new version overlay if present
        if "New Version Release Notes" in await page.content():
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

        # The index performs its initial DataTables draw on load; ensure the processing spinner
        # clears (guards against a wedged index spinner, e.g. a throwing drawCallback).
        await assert_no_spinner(page)

        # switch to compact/table mode
        await pcm.assert_ok()
        pcm.clear()

        await switch_display_mode(page, "compact")
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
        await title_header.click()
        await page.wait_for_load_state("networkidle")
        # The header sort class is set during the (async) DataTables redraw, which can land after
        # networkidle; wait for the index spinner to clear before reading post-draw DOM state.
        await assert_no_spinner(page)

        # verify header has sorting_desc class
        header_class = await title_header.get_attribute("class") or ""
        assert "sorting_desc" in header_class, (
            f"Expected sorting_desc on title header after click, got class={header_class!r}"
        )

        # verify title descending order as rendered: C, B, A
        expected_desc = ["archive C", "archive B", "archive A"]
        sorted_titles = await read_rendered_titles(page, 3, expected_desc)
        assert sorted_titles == expected_desc, (
            f"Expected descending title sort, got: {sorted_titles}"
        )

        # click title header again to sort ascending
        pcm.clear()

        await title_header.click()
        await page.wait_for_load_state("networkidle")
        await assert_no_spinner(page)

        # verify header has sorting_asc class
        header_class = await title_header.get_attribute("class") or ""
        assert "sorting_asc" in header_class, (
            f"Expected sorting_asc on title header after second click, got class={header_class!r}"
        )

        # verify title ascending order as rendered: A, B, C
        expected_asc = ["archive A", "archive B", "archive C"]
        sorted_titles = await read_rendered_titles(page, 3, expected_asc)
        assert sorted_titles == expected_asc, (
            f"Expected ascending title sort, got: {sorted_titles}"
        )

        await pcm.assert_ok()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
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

    async def assert_header_sort(page: playwright.async_api._generated.Page, header_locator: playwright.async_api._generated.Locator, expected_titles: list[str], expected_direction: str) -> None:
        await header_locator.click()
        await page.wait_for_load_state("networkidle")
        await assert_no_spinner(page)

        header_class = await header_locator.get_attribute("class") or ""
        assert f"sorting_{expected_direction}" in header_class, (
            f"Expected sorting_{expected_direction} on header, got class={header_class!r}"
        )

        titles = await read_rendered_titles(page, num_archives, expected_titles)
        assert titles == expected_titles, (
            f"Expected {expected_direction} order {expected_titles}, got {titles}"
        )

    # >>>>> UI STAGE >>>>>
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

        await page.goto(lrr_client.lrr_base_url, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_load_state("networkidle")

        if "New Version Release Notes" in await page.content():
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

        # switch to compact mode
        await switch_display_mode(page, "compact")
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

        await pcm.assert_ok()
        pcm.clear()
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

        await pcm.assert_ok()
        pcm.clear()
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

        await pcm.assert_ok()
        # <<<<< EDIT COLUMN 3 NAMESPACE <<<<<
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_index_page(lrr_client: LRRClient) -> None:
    """
    Test that the index page loads without errors.

    1. Verify the server has no archives.
    2. Navigate to index page.
    3. Expect the index to render no archive entries.
    4. Expect no HTTP errors and no console errors.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_load_state("networkidle")
        await assert_no_spinner(page)

        rendered = await read_rendered_titles(page, 0, timeout_ms=2000)
        assert rendered == [], (
            f"Expected no archives rendered on an empty library, got: {rendered}"
        )

        # dismiss new version overlay if present
        if "New Version Release Notes" in await page.content():
            await page.keyboard.press("Escape")

        await pcm.assert_ok()
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
    3. Select "artist" from dropdown.
       - Expect dropdown retains "artist" after DataTables re-draw.
       - Expect the rendered archives sorted by artist namespace (artist:Bob last in asc).
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
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

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
        pcm.clear()

        await sort_dropdown.select_option("artist")
        await page.wait_for_load_state("networkidle")
        await assert_no_spinner(page)

        sort_value = await sort_dropdown.input_value()
        assert sort_value == "artist", (
            f"Sort dropdown should show 'artist' after selecting it. Got '{sort_value}'."
        )

        # verify the rendered order reflects artist-sorted order
        sorted_titles = await read_rendered_titles(page, 3)
        assert len(sorted_titles) == 3, f"Expected 3 archives rendered, got {len(sorted_titles)}"
        # archives 0,1 have artist:Alice, archive 2 has artist:Bob; asc order => Alice first
        assert sorted_titles[-1] == "test archive 2", (
            f"Expected 'test archive 2' (artist:Bob) last in ascending artist sort, got order: {sorted_titles}"
        )

        # switch to compact/table mode and verify custom column headers are visible
        await pcm.assert_ok()
        pcm.clear()

        await switch_display_mode(page, "compact")
        await page.wait_for_load_state("networkidle")

        artist_header = page.locator("#customheader1")
        await artist_header.wait_for(state="visible", timeout=5000)
        series_header = page.locator("#customheader2")
        await series_header.wait_for(state="visible", timeout=5000)

        # switch back to thumbnail mode and select series sort
        await pcm.assert_ok()
        pcm.clear()

        await switch_display_mode(page, "thumbnail")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(500)

        # select series from dropdown and verify sort order
        await sort_dropdown.select_option("series")
        await page.wait_for_load_state("networkidle")
        await assert_no_spinner(page)

        sort_value = await sort_dropdown.input_value()
        assert sort_value == "series", (
            f"Sort dropdown should show 'series' after selecting it. Got '{sort_value}'."
        )

        # verify the rendered order reflects series-sorted order
        sorted_titles = await read_rendered_titles(page, 3)
        assert len(sorted_titles) == 3, f"Expected 3 archives rendered, got {len(sorted_titles)}"
        # archive 2 has series:Another, archives 0,1 have series:Test; asc => Another first
        assert sorted_titles[0] == "test archive 2", (
            f"Expected 'test archive 2' (series:Another) first in ascending series sort, got order: {sorted_titles}"
        )

        # switch back to title sort
        await pcm.assert_ok()
        pcm.clear()

        await sort_dropdown.select_option("title")
        await page.wait_for_load_state("networkidle")

        sort_value = await sort_dropdown.input_value()
        assert sort_value == "title", (
            f"Sort dropdown should show 'title' after selecting it. Got '{sort_value}'."
        )

        await pcm.assert_ok()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.xfail(reason="date_added is hardcoded in index.html.tt2 sort dropdown, not filtered by excluded namespaces", strict=False)
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
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

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

        await pcm.assert_ok()
    # <<<<< SETTINGS STAGE <<<<<

    # verify /api/info returns the excluded namespaces
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {error.status}): {error.error}"

    # >>>>> UI STAGE >>>>>
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

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

        await pcm.assert_ok()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.failing
async def test_new_archive(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Test that a newly uploaded archive is marked as new wherever archives are listed.

    The three listings do not share a data source, and only /api/* is schema-validated
    by OpenAPI, so each reads `isnew` back in a different form.

    1. Upload an archive, rebuild stat hash, confirm the API flags it new.
    2. Open the index with the carousel in "New Archives" mode.
       - Expect the marker on the grid entry (grid reads /search).
       - Expect the marker on the carousel slide (carousel reads /api/search).
    3. Open the batch tagger.
       - Expect the marker on the archive's list entry (batch reads /api/archives).
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    title = "new archive"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        save_path = create_archive_file(tmpdir, "new-archive", 3)
        response, error = await upload_archive(
            lrr_client, save_path, save_path.name, semaphore, title=title, tags="",
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> STAT REBUILD STAGE >>>>>
    await trigger_stat_rebuild(lrr_client)
    # <<<<< STAT REBUILD STAGE <<<<<

    metadata, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    assert not error, f"Failed to read archive metadata (status {error.status}): {error.error}"
    assert metadata.isnew, f"Uploaded archive {arcid} is not flagged new"

    # Archive IDs are hex and may start with a digit, which no CSS id selector accepts.
    arc_selector = f'[id="{arcid}"]'

    # >>>>> UI STAGE >>>>>
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page
        await page.add_init_script("localStorage.setItem('carouselType', 'inbox');")

        # login to access the batch tagger
        await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
        await page.wait_for_load_state("networkidle")
        await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
        await page.get_by_role("button", name="Login").click()
        await page.wait_for_load_state("networkidle")
        pcm.clear()

        await page.goto(lrr_client.lrr_base_url, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_load_state("networkidle")

        # dismiss new version overlay if present
        if "New Version Release Notes" in await page.content():
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

        await assert_no_spinner(page)

        grid_entry = page.locator(f"#thumbs_container div.id1{arc_selector}")
        await grid_entry.wait_for(state="attached", timeout=10000)
        grid_text = await grid_entry.inner_text()

        carousel_entry = page.locator(f".index-carousel-container div.id1{arc_selector}")
        await carousel_entry.wait_for(state="attached", timeout=10000)
        carousel_text = await carousel_entry.inner_text()

        await page.goto(f"{lrr_client.lrr_base_url}/batch", timeout=60000)
        await page.wait_for_load_state("networkidle")
        batch_entry = page.locator(f"#archivelist label[for='{arcid}']")
        await batch_entry.wait_for(state="visible", timeout=10000)
        batch_text = await batch_entry.inner_text()

        await pcm.assert_ok()
    # <<<<< UI STAGE <<<<<

    # >>>>> VERIFY STAGE >>>>>
    # Thumbnail entries render the status icons and the title on separate lines.
    grid_lines = [line.strip() for line in grid_text.splitlines() if line.strip()]
    assert grid_lines[:2] == [NEW_MARKER, title], (
        f"Expected the grid entry to open with [{NEW_MARKER!r}, {title!r}], got {grid_lines[:2]!r}"
    )

    carousel_lines = [line.strip() for line in carousel_text.splitlines() if line.strip()]
    assert carousel_lines[:2] == [NEW_MARKER, title], (
        f"Expected the carousel slide to open with [{NEW_MARKER!r}, {title!r}], got {carousel_lines[:2]!r}"
    )

    assert batch_text == f"{title} {NEW_MARKER}", (
        f"Expected the batch entry to read '{title} {NEW_MARKER}', got {batch_text!r}"
    )
    # <<<<< VERIFY STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)
