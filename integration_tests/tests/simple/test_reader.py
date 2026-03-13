"""
All simple tests which mainly have to do with self-contained reader functionality,
such as page navigation and viewing, manga mode, slideshow, ToC, etc.
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

import playwright
import playwright.async_api
import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import (
    AddTocEntryRequest,
    ExtractArchiveRequest,
    GetArchiveMetadataRequest,
    GetArchivePageRequest,
)

from aio_lanraragi_tests.common import DEFAULT_LRR_PASSWORD, LRR_INDEX_TITLE
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
    assert_no_spinner,
    get_image_bytes_from_responses,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_slideshow(
    lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Tests basic slideshow functionality.

    1. Uploads 1 archives to LRR
    2. Opens archive (5 pages)
    3. Adjust slideshow duration to 1s
    3. Start slideshow mode with 1s duration per page
    4. Wait 9 seconds
    5. Expect the final page (check page count + image bytes hash equal)
    6. Check reading progress is complete via API (slideshow affects reading progress)
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_title = "test archive title"
        archive_name = "test-archive-name"
        archive_path = create_archive_file(Path(tmpdir), archive_name, num_pages=5)
        response, error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            semaphore,
            title=archive_title,
            tags="",
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)

            # configure slideshow duration
            LOGGER.info("Configuring slideshow duration to 1s")
            await page.keyboard.press("o") # open options
            await page.locator("#settingsOverlay").wait_for(state="visible")
            header = await page.locator("#settingsOverlay h2.ih").first.text_content()
            assert header.strip() == "Reader Options", f"Expected 'Reader Options' header, got {header!r}"
            await page.locator("#auto-next-page-input").fill("1")
            await page.locator("#auto-next-page-apply").click()
            await page.keyboard.press("Tab") # blur input so keyboard shortcuts work
            await page.keyboard.press("o") # close options
            await page.locator("#settingsOverlay").wait_for(state="hidden")

            LOGGER.info("Starting slideshow with keypress n")
            await page.keyboard.press("n")

            LOGGER.info("Waiting for 9 seconds...")
            await page.wait_for_timeout(9000)

            # Verify slideshow landed on the final page.
            current_page_text = await page.locator("span.current-page").first.text_content()
            assert current_page_text.strip() == "5", f"Expected current page to be 5, got {current_page_text!r}"

            # Get displayed image bytes from the captured browser responses.
            img_src = await page.locator("#img").get_attribute("src")
            browser_image_bytes = await get_image_bytes_from_responses(responses, img_src)

            # Get 5th page image bytes via API.
            response, error = await lrr_client.archive_api.extract_archive(ExtractArchiveRequest(arcid=arcid))
            assert not error, f"Extract failed (status {error.status}): {error.error}"
            response, error = await lrr_client.archive_api.get_archive_page(GetArchivePageRequest(page_url=response.pages[-1]))
            assert not error, f"Failed to download last page (status {error.status}): {error.error}"

            assert browser_image_bytes == response.data, "Browser image bytes do not match API page bytes for the last page."

            response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
            assert response.progress == 5, "Archive reading progress is not updated to last page after slideshow."

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("preload")
async def test_webkit_reader_preload(
    lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    - Issue: https://github.com/Difegue/LANraragi/issues/1433
    - PR: https://github.com/Difegue/LANraragi/pull/1459

    In WebKit, when preloading is enabled and the user moves to the next page,
    the reader should serve the already preloaded image without re-fetching the
    same page image URL over the network.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_title = "test archive title"
        archive_name = "test-archive-name"
        archive_path = create_archive_file(Path(tmpdir), archive_name, num_pages=6)
        response, error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            semaphore,
            title=archive_title,
            tags="",
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.webkit.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)

            # Give preload requests a chance to complete before turning page
            # responses will be growing so we need to capture responses_before_page_turn now
            await page.wait_for_timeout(1000)
            responses_before_page_turn = len(responses)

            # Trigger one page turn
            await page.keyboard.press("ArrowRight")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(1000)

            # check that prefetch occurred.
            next_page_url = f"/api/archives/{arcid}/page?path={archive_name}-pg-2.png"
            prefetched_url = None
            for response in responses[:responses_before_page_turn]:
                if response.request.method != "GET":
                    continue
                if next_page_url not in response.url:
                    continue
                prefetched_url = response.url
                break
            assert prefetched_url is not None, (
                "Expected next page to be preloaded before page turn, but no GET was observed. "
                f"next_page_url_fragment={next_page_url}"
            )

            # check that prefetch URL doesn't appear again (GET refetch or HEAD size request).
            for response in responses[responses_before_page_turn:]:
                if response.request.method not in ("GET", "HEAD"):
                    continue
                if next_page_url not in response.url:
                    continue
                assert False, (
                    f"Detected {response.request.method} request for prefetched page during page turn: {response.url}"
                )

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.regression
async def test_double_page_navigation(
    lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Verify that forward and backward navigation in double-page mode serves
    the correct page images at each step.

    Feature regression check:
    - PR: https://github.com/Difegue/LANraragi/pull/1459
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_title = "test archive title"
        archive_name = "test-archive-name"
        archive_path = create_archive_file(Path(tmpdir), archive_name, num_pages=6)
        response, error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            semaphore,
            title=archive_title,
            tags="",
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # Expected query parameter suffix for a given 0-indexed page.
    def expected_page_path(page_index: int) -> str:
        return f"path={archive_name}-pg-{page_index + 1}.png"

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)

            LOGGER.info("Enabling double-page mode.")
            await page.keyboard.press("p")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)

            # Page 0 (single, cover) -> pages 1+2 (double)
            LOGGER.info("Navigating to pages 1+2.")
            await page.keyboard.press("ArrowRight")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)

            display_class = await page.locator("#display").get_attribute("class") or ""
            assert "double-mode" in display_class, f"Expected double-mode class on #display, got class={display_class!r}"
            img_src = await page.locator("#img").get_attribute("src")
            assert img_src.endswith(expected_page_path(1)), f"#img expected {expected_page_path(1)}, got src={img_src!r}"
            img_dp_src = await page.locator("#img_doublepage").get_attribute("src")
            assert img_dp_src.endswith(expected_page_path(2)), f"#img_doublepage expected {expected_page_path(2)}, got src={img_dp_src!r}"

            # Pages 1+2 -> pages 3+4
            LOGGER.info("Navigating forward to pages 3+4.")
            await page.keyboard.press("ArrowRight")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)

            img_src = await page.locator("#img").get_attribute("src")
            assert img_src.endswith(expected_page_path(3)), f"#img expected {expected_page_path(3)}, got src={img_src!r}"
            img_dp_src = await page.locator("#img_doublepage").get_attribute("src")
            assert img_dp_src.endswith(expected_page_path(4)), f"#img_doublepage expected {expected_page_path(4)}, got src={img_dp_src!r}"

            # Pages 3+4 -> pages 1+2 (navigate back)
            LOGGER.info("Navigating back to pages 1+2.")
            await page.keyboard.press("ArrowLeft")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)

            img_src = await page.locator("#img").get_attribute("src")
            assert img_src.endswith(expected_page_path(1)), f"#img expected {expected_page_path(1)}, got src={img_src!r}"
            img_dp_src = await page.locator("#img_doublepage").get_attribute("src")
            assert img_dp_src.endswith(expected_page_path(2)), f"#img_doublepage expected {expected_page_path(2)}, got src={img_dp_src!r}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_handler_resource_management(
    lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Verify that repeated forward/backward page navigation does not
    accumulate event handlers on reader image elements.

    After several navigation cycles revisiting the same pages, each
    image element should carry at most one 'load' event handler.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_title = "test archive title"
        archive_name = "test-archive-name"
        archive_path = create_archive_file(Path(tmpdir), archive_name, num_pages=6)
        response, error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            semaphore,
            title=archive_title,
            tags="handler,resource,management",
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    def expected_page_path(page_index: int) -> str:
        return f"path={archive_name}-pg-{page_index + 1}.png"

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)

            # Verify initial state: page 0 displayed
            img_src = await page.locator("#img").get_attribute("src")
            assert img_src.endswith(expected_page_path(0)), (
                f"Initial #img expected {expected_page_path(0)}, got src={img_src!r}"
            )

            # Navigate forward/backward N cycles, revisiting the same pages.
            # Each cycle: page 0 -> page 1 -> page 0.
            # If handlers accumulate, page 0's Image object will gain one
            # additional 'load' handler per revisit.
            num_cycles = 4
            for cycle in range(num_cycles):
                LOGGER.info(f"Navigation cycle {cycle + 1}/{num_cycles}: forward to page 1.")
                await page.keyboard.press("ArrowRight")
                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(300)

                img_src = await page.locator("#img").get_attribute("src")
                assert img_src.endswith(expected_page_path(1)), (
                    f"Cycle {cycle + 1} forward: #img expected {expected_page_path(1)}, got src={img_src!r}"
                )

                LOGGER.info(f"Navigation cycle {cycle + 1}/{num_cycles}: back to page 0.")
                await page.keyboard.press("ArrowLeft")
                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(300)

                img_src = await page.locator("#img").get_attribute("src")
                assert img_src.endswith(expected_page_path(0)), (
                    f"Cycle {cycle + 1} back: #img expected {expected_page_path(0)}, got src={img_src!r}"
                )

            # Verify image element attributes are preserved after all cycles
            img_class = await page.locator("#img").get_attribute("class")
            assert img_class == "reader-image", (
                f"#img class expected 'reader-image', got {img_class!r}"
            )
            img_priority = await page.locator("#img").get_attribute("fetchpriority")
            assert img_priority == "high", (
                f"#img fetchpriority expected 'high', got {img_priority!r}"
            )

            # Assert handler count via jQuery internal event data.
            # jQuery._data(element, 'events') exposes handlers attached via .on().
            # After N revisits of the same page, a well-managed reader should have
            # at most 1 'load' handler on each image element.
            load_handler_count = await page.evaluate(
                """() => {
                    const el = document.getElementById('img');
                    const events = jQuery._data(el, 'events');
                    if (!events || !events.load) return 0;
                    return events.load.length;
                }"""
            )
            assert load_handler_count == 1, (
                f"Expected 1 load handler on #img after {num_cycles} navigation cycles, "
                f"found {load_handler_count} (handlers are accumulating on cached Image objects)"
            )

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("navigation")
async def test_archive_navigation(
    lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Test navigation capabilities.

    1. Upload 3 archives, 3 pages each.
    2. Go to index page and click on first archive; collect the search draw metadata from
        network (to know what the 2nd and 3rd archives are supposed to be).
    3. Navigate (via "right" key press) from first archive to 3rd archive.
        - Confirm that the 3rd and 6th key presses correspond to changes in archive title
          and that the page bytes are the first pages of the 2nd and 3rd archives resp.
        - Confirm when at last page of 3rd archive, right keypress does nothing.
    4. Navigate to 2nd from 3rd archive via "[".
    5. Navigate from 2nd to 1st archive via "left" keypress.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    archive_names = ["archive-1", "archive-2", "archive-3"]
    archive_titles = ["Archive 1", "Archive 2", "Archive 3"]
    with tempfile.TemporaryDirectory() as tmpdir:
        for name, title in zip(archive_names, archive_titles):
            archive_path = create_archive_file(Path(tmpdir), name, num_pages=3)
            response, error = await upload_archive(
                lrr_client,
                archive_path,
                archive_path.name,
                semaphore,
                title=title,
                tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # Go to index page, wait for DT draw to complete.
            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_INDEX_TITLE

            # exit overlay
            if "New Version Release Notes" in await page.content():
                LOGGER.info("Closing new releases overlay.")
                await page.keyboard.press("Escape")

            # Collect the datatables search response from the network waterfall
            # to determine the display order of archives.
            search_response_body = None
            for resp in responses:
                if "/search" not in resp.url or resp.request.method != "GET" or resp.status != 200:
                    continue
                body = json.loads(await resp.text())
                if "data" in body and len(body["data"]) == 3:
                    search_response_body = body
                    break
            assert search_response_body is not None, "Did not find datatables search response in network waterfall"
            dt_arcids = []
            dt_titles = []
            for entry in search_response_body["data"]:
                dt_arcids.append(entry["arcid"])
                dt_titles.append(entry["title"])
            LOGGER.info(f"Datatables archive order: {list(zip(dt_titles, dt_arcids))}")

            # Assert and clear index page responses before navigating to reader.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            # Open the first archive from the thumbnail view (default index mode).
            # #thumbs_container links trigger the datatables click handler,
            # which sets sessionStorage.navigationState = 'datatables'.
            LOGGER.info(f"Opening first archive: {dt_titles[0]}")
            await page.locator("#thumbs_container a", has_text=dt_titles[0]).first.click()
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)
            assert f"id={dt_arcids[0]}" in page.url, f"Expected first archive in URL, got {page.url}"

            # Navigate forward via ArrowRight through all 3 archives.
            # 3 pages per archive, so presses at index 2 and 5 are archive transitions.
            for keypress_count in range(6):
                if keypress_count in (2, 5):
                    # Full page navigation ahead; assert and clear responses at boundary.
                    await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
                    await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
                    responses.clear()
                    console_evts.clear()

                LOGGER.info(f"ArrowRight press {keypress_count + 1}/6")
                await page.keyboard.press("ArrowRight")

                if keypress_count in (2, 5):
                    expected_idx = 1 if keypress_count == 2 else 2
                    await page.wait_for_url(lambda url, eid=dt_arcids[expected_idx]: eid in url)

                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(500)

                # Archive transition: 1st archive -> 2nd archive
                if keypress_count == 2:
                    LOGGER.info("Verifying archive transition to 2nd archive.")
                    title_text = await page.locator("#archive-title").text_content()
                    assert dt_titles[1] in title_text, f"Expected title containing {dt_titles[1]!r}, got {title_text!r}"

                    img_src = await page.locator("#img").get_attribute("src")
                    browser_image_bytes = await get_image_bytes_from_responses(responses, img_src)
                    response, error = await lrr_client.archive_api.extract_archive(ExtractArchiveRequest(arcid=dt_arcids[1]))
                    assert not error, f"Extract failed (status {error.status}): {error.error}"
                    response, error = await lrr_client.archive_api.get_archive_page(GetArchivePageRequest(page_url=response.pages[0]))
                    assert not error, f"Failed to get first page of 2nd archive (status {error.status}): {error.error}"
                    assert browser_image_bytes == response.data, "Browser image bytes do not match API first page of 2nd archive"

                # Archive transition: 2nd archive -> 3rd archive
                if keypress_count == 5:
                    LOGGER.info("Verifying archive transition to 3rd archive.")
                    title_text = await page.locator("#archive-title").text_content()
                    assert dt_titles[2] in title_text, f"Expected title containing {dt_titles[2]!r}, got {title_text!r}"

                    img_src = await page.locator("#img").get_attribute("src")
                    browser_image_bytes = await get_image_bytes_from_responses(responses, img_src)
                    response, error = await lrr_client.archive_api.extract_archive(ExtractArchiveRequest(arcid=dt_arcids[2]))
                    assert not error, f"Extract failed (status {error.status}): {error.error}"
                    response, error = await lrr_client.archive_api.get_archive_page(GetArchivePageRequest(page_url=response.pages[0]))
                    assert not error, f"Failed to get first page of 3rd archive (status {error.status}): {error.error}"
                    assert browser_image_bytes == response.data, "Browser image bytes do not match API first page of 3rd archive"

            # Navigate to last page of 3rd archive.
            # After the loop we are at page 1 (first page) of 3rd archive.
            # Two more ArrowRight presses reach page 3 (last page).
            LOGGER.info("Navigating to last page of 3rd archive.")
            for _ in range(2):
                await page.keyboard.press("ArrowRight")
                await page.wait_for_load_state("networkidle")
                await assert_no_spinner(page)
                await page.wait_for_timeout(500)
            current_page_text = await page.locator("span.current-page").first.text_content()
            assert current_page_text.strip() == "3", f"Expected page 3 (last), got {current_page_text!r}"
            assert f"id={dt_arcids[2]}" in page.url, "Expected to still be in 3rd archive at last page"

            # Verify ArrowRight does nothing at last page of last archive.
            LOGGER.info("Verifying ArrowRight is idempotent at last page of last archive.")
            current_url = page.url
            await page.keyboard.press("ArrowRight")
            await page.wait_for_timeout(1000)
            assert page.url == current_url, "URL changed when pressing right at last page of last archive"
            page_text_after = await page.locator("span.current-page").first.text_content()
            assert page_text_after.strip() == "3", "Page changed at last page of last archive"

            # Navigate from 3rd to 2nd archive via "[".
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            LOGGER.info("Navigating from 3rd to 2nd archive via '[' key.")
            await page.keyboard.press("[")
            await page.wait_for_url(lambda url: dt_arcids[1] in url)
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)
            title_text = await page.locator("#archive-title").text_content()
            assert dt_titles[1] in title_text, f"Expected title containing {dt_titles[1]!r} after '[', got {title_text!r}"
            landing_page_text = await page.locator("span.current-page").first.text_content()
            assert landing_page_text.strip() == "1", f"Expected '[' to land on page 1, got {landing_page_text!r}"

            # Navigate from 2nd to 1st archive via ArrowLeft.
            # "[" landed on page 1 (first page), so ArrowLeft triggers readPreviousArchive.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            LOGGER.info("Navigating from 2nd to 1st archive via ArrowLeft.")
            await page.keyboard.press("ArrowLeft")
            await page.wait_for_url(lambda url: dt_arcids[0] in url)
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)
            title_text = await page.locator("#archive-title").text_content()
            assert dt_titles[0] in title_text, f"Expected title containing {dt_titles[0]!r} after ArrowLeft, got {title_text!r}"

            # check browser traffic is OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("navigation")
async def test_slideshow_continue_navigation(
    lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Test that slideshow continues to the next archive.

    1. Upload 3 archives, 3 pages each.
    2. Go to index page and click on first archive; collect the search draw metadata from
        network (to know what the 2nd and 3rd archives are supposed to be).
    3. Configure slideshow to 1s per page and start slideshow.
    4. Wait for a period of time, and assert that the 3rd page of 3rd archive is reached.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    archive_names = ["archive-1", "archive-2", "archive-3"]
    archive_titles = ["Archive 1", "Archive 2", "Archive 3"]
    with tempfile.TemporaryDirectory() as tmpdir:
        for name, title in zip(archive_names, archive_titles):
            archive_path = create_archive_file(Path(tmpdir), name, num_pages=3)
            response, error = await upload_archive(
                lrr_client,
                archive_path,
                archive_path.name,
                semaphore,
                title=title,
                tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # Go to index page, wait for DT draw to complete.
            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_INDEX_TITLE

            # exit overlay
            if "New Version Release Notes" in await page.content():
                LOGGER.info("Closing new releases overlay.")
                await page.keyboard.press("Escape")

            # Collect the datatables search response from the network waterfall
            # to determine the display order of archives.
            search_response_body = None
            for resp in responses:
                if "/search" not in resp.url or resp.request.method != "GET" or resp.status != 200:
                    continue
                body = json.loads(await resp.text())
                if "data" in body and len(body["data"]) == 3:
                    search_response_body = body
                    break
            assert search_response_body is not None, "Did not find datatables search response in network waterfall"
            dt_arcids = []
            dt_titles = []
            for entry in search_response_body["data"]:
                dt_arcids.append(entry["arcid"])
                dt_titles.append(entry["title"])
            LOGGER.info(f"Datatables archive order: {list(zip(dt_titles, dt_arcids))}")

            # Assert and clear index page responses before navigating to reader.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            # Open the first archive from the thumbnail view.
            LOGGER.info(f"Opening first archive: {dt_titles[0]}")
            await page.locator("#thumbs_container a", has_text=dt_titles[0]).first.click()
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)
            assert f"id={dt_arcids[0]}" in page.url, f"Expected first archive in URL, got {page.url}"

            # Configure slideshow duration to 1s and start.
            LOGGER.info("Configuring slideshow duration to 1s")
            await page.keyboard.press("o")
            await page.locator("#settingsOverlay").wait_for(state="visible")
            await page.locator("#auto-next-page-input").fill("1")
            await page.locator("#auto-next-page-apply").click()
            await page.keyboard.press("Tab")
            await page.keyboard.press("o")
            await page.locator("#settingsOverlay").wait_for(state="hidden")

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            LOGGER.info("Starting slideshow with keypress n")
            await page.keyboard.press("n")

            # Wait for the slideshow to cross into the 2nd archive.
            LOGGER.info("Waiting for slideshow to reach 2nd archive...")
            await page.wait_for_url(
                lambda url: dt_arcids[1] in url,
                timeout=30000,
            )
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            LOGGER.info("Slideshow reached 2nd archive.")
            title_text = await page.locator("#archive-title").text_content()
            assert dt_titles[1] in title_text, f"Expected title containing {dt_titles[1]!r}, got {title_text!r}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            # Wait for the slideshow to cross into the 3rd archive.
            LOGGER.info("Waiting for slideshow to reach 3rd archive...")
            await page.wait_for_url(
                lambda url: dt_arcids[2] in url,
                timeout=30000,
            )
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            LOGGER.info("Slideshow reached 3rd archive.")
            title_text = await page.locator("#archive-title").text_content()
            assert dt_titles[2] in title_text, f"Expected title containing {dt_titles[2]!r}, got {title_text!r}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            responses.clear()
            console_evts.clear()

            # Wait for the slideshow to reach the last page of the 3rd archive.
            LOGGER.info("Waiting for slideshow to reach last page of 3rd archive...")
            await page.wait_for_function(
                """() => {
                    const el = document.querySelector('span.current-page');
                    return el && el.textContent.trim() === '3';
                }""",
                timeout=15000,
            )

            # Give slideshow time to stop (it should not advance further).
            await page.wait_for_timeout(2000)
            current_page_text = await page.locator("span.current-page").first.text_content()
            assert current_page_text.strip() == "3", f"Expected page 3 (last), got {current_page_text!r}"
            assert f"id={dt_arcids[2]}" in page.url, "Expected to be in 3rd archive at last page"

            # check browser traffic is OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE (slideshow_continue_navigation) <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_toc_reader(
    lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Test ToC reader UI.

    1. Upload 1 archive (10 pages), add 3 chapters via API (pages 1, 4, 7).
    2. Login via browser (required for admin-gated ToC icons).
    3. Open reader, open overlay, verify chapter selector has 3 options.
    4. Verify overlay scoping: Chapter 1 shows 3 thumbnails (pages 1-3).
    5. Navigate to Chapter 2 via dropdown, verify page jumps to 4, overlay shows 3 thumbnails.
    6. Navigate to Chapter 3, verify overlay shows 4 thumbnails (pages 7-10).
    7. Add chapter at page 2 via UI, verify dropdown grows to 4 options.
    8. Edit chapter title via UI, verify rename persists via API.
    9. Delete chapter via UI, verify deleted name absent from dropdown, 3 toc entries via API.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "archive_1", num_pages=10)
        response, error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            semaphore,
            title="Title 1",
            tags="artist:a",
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> ADD TOC VIA API >>>>>
    for pg, title in [(1, "Chapter 1"), (4, "Chapter 2"), (7, "Chapter 3")]:
        response, error = await lrr_client.archive_api.add_toc_entry(AddTocEntryRequest(arcid=arcid, page=pg, title=title))
        assert not error, f"Failed to add toc entry page={pg} (status {error.status}): {error.error}"
    del response, error
    # <<<<< ADD TOC VIA API <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # login to get admin access (required for add/edit/delete toc icons)
            await page.goto(f"{lrr_client.lrr_base_url}/login")
            await page.wait_for_load_state("networkidle")
            await page.fill("#pw_field", DEFAULT_LRR_PASSWORD)
            await page.click("input[type='submit']")
            await page.wait_for_load_state("networkidle")
            responses.clear()
            console_evts.clear()

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)

            # open overlay
            await page.keyboard.press("q")
            await page.locator("#archivePagesOverlay").wait_for(state="visible")

            # verify chapter selector has 3 options
            chapter_select = page.locator("#chapter-select")
            options = chapter_select.locator("option")
            option_count = await options.count()
            assert option_count == 3, f"Expected 3 chapter options, got {option_count}"

            option_texts = []
            for i in range(option_count):
                option_texts.append(await options.nth(i).text_content())
            assert "Chapter 1" in option_texts[0]
            assert "Chapter 2" in option_texts[1]
            assert "Chapter 3" in option_texts[2]

            # verify overlay scoping for chapter 1: pages 1-3 (3 thumbnails)
            thumbnails = page.locator("#pages-section .quick-thumbnail")
            ch1_count = await thumbnails.count()
            assert ch1_count == 3, f"Expected 3 thumbnails for Chapter 1, got {ch1_count}"

            # navigate to chapter 2 via dropdown
            LOGGER.debug("Selecting Chapter 2 from dropdown.")
            await chapter_select.select_option(value="4")
            await page.wait_for_timeout(500)

            # verify page counter jumped to page 4
            current_page_text = await page.locator("span.current-page").first.text_content()
            assert current_page_text.strip() == "4", f"Expected page 4 after chapter select, got {current_page_text!r}"

            # verify overlay scoping for chapter 2: pages 4-6 (3 thumbnails)
            ch2_count = await thumbnails.count()
            assert ch2_count == 3, f"Expected 3 thumbnails for Chapter 2, got {ch2_count}"

            # navigate to chapter 3 via dropdown
            LOGGER.debug("Selecting Chapter 3 from dropdown.")
            await chapter_select.select_option(value="7")
            await page.wait_for_timeout(500)

            # verify overlay scoping for chapter 3: pages 7-10 (4 thumbnails)
            ch3_count = await thumbnails.count()
            assert ch3_count == 4, f"Expected 4 thumbnails for Chapter 3, got {ch3_count}"

            # >>>>> ADD CHAPTER VIA UI >>>>>
            # navigate back to chapter 1 to add a chapter at page 2
            await chapter_select.select_option(value="1")
            await page.wait_for_timeout(1000)

            # wait for thumbnails to render
            await thumbnails.first.wait_for(state="visible")

            # force-click the add-chapter icon on the 2nd thumbnail (page 2)
            add_toc_icon = page.locator("#pages-section .quick-thumbnail").nth(1).locator(".add-toc")
            await add_toc_icon.click(force=True)

            # fill SweetAlert2 dialog
            await page.get_by_role("textbox").fill("Chapter 1.5")
            await page.get_by_role("button", name="OK").click()

            # overlay reopens only after PUT + metadata reload completes
            await page.locator("#archivePagesOverlay").wait_for(state="hidden")
            await page.locator("#archivePagesOverlay").wait_for(state="visible")

            # verify dropdown now has 4 options
            option_count = await options.count()
            assert option_count == 4, f"Expected 4 chapter options after add, got {option_count}"
            # <<<<< ADD CHAPTER VIA UI <<<<<

            # >>>>> EDIT CHAPTER VIA UI >>>>>
            LOGGER.debug("Editing chapter title via UI.")
            edit_icon = page.locator(".edit-toc").first
            await edit_icon.click()

            await page.get_by_role("textbox").fill("Chapter 1 Renamed")
            await page.get_by_role("button", name="OK").click()

            # overlay reopens only after PUT + metadata reload completes
            await page.locator("#archivePagesOverlay").wait_for(state="hidden")
            await page.locator("#archivePagesOverlay").wait_for(state="visible")

            # verify via API that the rename took effect
            response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
            assert not error, f"Failed to get metadata (status {error.status}): {error.error}"
            renamed_entry = None
            for entry in response.toc:
                if entry.page == 1:
                    renamed_entry = entry
                    break
            assert renamed_entry is not None, "ToC entry for page 1 not found after edit"
            assert renamed_entry.name == "Chapter 1 Renamed", f"Expected renamed title, got {renamed_entry.name!r}"
            del response, error
            # <<<<< EDIT CHAPTER VIA UI <<<<<

            # >>>>> DELETE CHAPTER VIA UI >>>>>
            LOGGER.debug("Deleting chapter via UI.")
            delete_icon = page.locator(".remove-toc").first
            await delete_icon.click()

            # confirm SweetAlert2 deletion dialog
            await page.get_by_role("button", name="Yes, delete it!").click()

            # overlay reopens only after DELETE + metadata reload completes
            await page.locator("#archivePagesOverlay").wait_for(state="hidden")
            await page.locator("#archivePagesOverlay").wait_for(state="visible")

            # dropdown still shows 4 options: buildChapterObject adds an implicit
            # untitled chapter for pages before the first toc entry (now page 2)
            option_count = await options.count()
            assert option_count == 4, f"Expected 4 chapter options after delete, got {option_count}"
            first_option_text = await options.nth(0).text_content()
            assert "Chapter 1 Renamed" not in first_option_text, f"Deleted chapter still in dropdown: {first_option_text!r}"

            # verify deletion via API
            response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
            assert not error, f"Failed to get metadata (status {error.status}): {error.error}"
            assert len(response.toc) == 3, f"Expected 3 toc entries after UI delete, got {len(response.toc)}"
            del response, error
            # <<<<< DELETE CHAPTER VIA UI <<<<<

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<
