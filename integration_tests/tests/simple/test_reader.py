"""
All simple tests which mainly have to do with self-contained reader functionality,
such as page navigation and viewing, manga mode, slideshow, ToC, etc.
"""

import asyncio
import tempfile
from pathlib import Path

import playwright
import playwright.async_api
import pytest
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive


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
            page.on("response", lambda response: responses.append(response))

            await page.goto(f"{lrr_client.lrr_base_url}/reader?id={arcid}")
            await page.wait_for_load_state("networkidle")

            # Give preload requests a chance to complete before turning page
            # responses will be growing so we need to capture responses_before_page_turn now
            await page.wait_for_timeout(1000)
            responses_before_page_turn = len(responses)

            # Trigger one page turn
            await page.keyboard.press("ArrowRight")
            await page.wait_for_load_state("networkidle")
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
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<
