"""
All simple tests which mainly have to do with self-contained reader functionality,
such as page navigation and viewing, manga mode, slideshow, ToC, etc.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import playwright
import playwright.async_api
import pytest
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
    assert_no_spinner,
)

LOGGER = logging.getLogger(__name__)


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
    archive_title = "Safari Preload Regression Archive"

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "safari-preload-regression", num_pages=6)
        response, error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            semaphore,
            title=archive_title,
            tags="safari,preload,regression",
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
            next_page_url = f"/api/archives/{arcid}/page?path=safari-preload-regression-pg-2.png"
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

            # check that prefetch URL doesn't appear again.
            for response in responses[responses_before_page_turn:]:
                if response.request.method != "GET":
                    continue
                assert response.url != prefetched_url, f"Detected URL refetch for prefetched page during page turn: {prefetched_url}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_double_page_navigation(
    lrr_client: LRRClient, semaphore: asyncio.Semaphore,
):
    """
    Verify that forward and backward navigation in double-page mode serves
    the correct page images at each step.

    Feature regression check:
    - PR: https://github.com/Difegue/LANraragi/pull/1459
    """
    archive_title = "Double Page Navigation Archive"
    archive_name = "dbl-nav"

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), archive_name, num_pages=6)
        response, error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            semaphore,
            title=archive_title,
            tags="double-page,navigation",
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # Expected query parameter suffix for a given 0-indexed page.
    # Archive "dbl-nav" with 6 pages produces: dbl-nav-pg-1.png through dbl-nav-pg-6.png
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
            await assert_console_logs_ok(console_evts)
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
    archive_title = "Handler Resource Management Archive"
    archive_name = "handler-res"
    num_cycles = 4

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
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
            await assert_console_logs_ok(console_evts)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<
