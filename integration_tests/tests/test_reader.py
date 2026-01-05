"""
Reader UI integration tests for the LANraragi server.

These tests verify the reader's behavior across different browsers,
particularly around image preloading and caching.
"""

import asyncio
import logging
from collections import Counter
from pathlib import Path
import sys
import tempfile
from typing import Dict, Generator

import pytest
import pytest_asyncio
import playwright.async_api

from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.helpers import (
    create_archive_file,
    expect_no_error_logs,
    get_bounded_sem,
    upload_archive,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext

LOGGER = logging.getLogger(__name__)


# ===== FIXTURES =====
# Using port offset 12 to avoid conflicts with simple (10) and search (11)

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "reader_"


@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 12


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
async def lrr_client(environment: AbstractLRRDeploymentContext) -> Generator[LRRClient, None, None]:
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()


# ===== HELPER FUNCTIONS =====

async def _run_preload_duplicate_fetch_test(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
    browser_name: str,
) -> dict:
    """
    Core test logic for verifying preloaded images are not re-fetched.

    Returns a dict with test results including any duplicate requests found.
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
        save_path = create_archive_file(tmpdir, "preload-test", num_pages)

        response, error = await upload_archive(
            lrr_client, save_path, save_path.name, semaphore,
            title="Preload Test Archive", tags="test:preload"
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        archive_id = response.arcid
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser_type = getattr(p, browser_name)
        browser = await browser_type.launch()
        page = await browser.new_page()

        page_get_requests: list[str] = []

        def on_request(request: playwright.async_api.Request):
            url = request.url
            if request.method == "GET" and "/api/archives/" in url and "/page" in url:
                page_get_requests.append(url)

        page.on("request", on_request)

        reader_url = f"{lrr_client.lrr_base_url}/reader?id={archive_id}"
        await page.goto(reader_url, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")

        if "New Version Release Notes" in await page.content():
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

        await asyncio.sleep(1)
        await page.keyboard.press("ArrowRight")
        await asyncio.sleep(3)

        await browser.close()
    # <<<<< UI STAGE <<<<<

    # >>>>> VALIDATION >>>>>
    get_request_counts = Counter(page_get_requests)
    get_duplicates = {url: count for url, count in get_request_counts.items() if count > 1}

    expect_no_error_logs(environment)

    return {
        "browser": browser_name,
        "total_requests": len(page_get_requests),
        "unique_requests": len(get_request_counts),
        "duplicates": get_duplicates,
    }


# ===== TEST CASES =====

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_reader_preload_no_duplicate_fetch_chromium(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
):
    """
    Verify that preloaded images are not re-fetched when navigating pages (Chromium).

    Issue: https://github.com/Difegue/LANraragi/issues/1433
    """
    result = await _run_preload_duplicate_fetch_test(
        lrr_client, semaphore, environment, "chromium"
    )

    assert len(result["duplicates"]) == 0, (
        f"Preloaded images were re-fetched via GET in chromium! "
        f"This indicates the preloading mechanism is not properly caching images. "
        f"Duplicates: {result['duplicates']}"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="Webkit tests only run on macOS")
@pytest.mark.asyncio
@pytest.mark.playwright
# @pytest.mark.xfail(reason="Safari/webkit has duplicate fetch bug (https://github.com/Difegue/LANraragi/issues/1433)")
async def test_reader_preload_no_duplicate_fetch_webkit(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
):
    """
    Verify that preloaded images are not re-fetched when navigating pages (Webkit/Safari).

    Issue: https://github.com/Difegue/LANraragi/issues/1433
    """
    result = await _run_preload_duplicate_fetch_test(
        lrr_client, semaphore, environment, "webkit"
    )

    assert len(result["duplicates"]) == 0, (
        f"Preloaded images were re-fetched via GET in webkit! "
        f"This indicates the preloading mechanism is not properly caching images. "
        f"Duplicates: {result['duplicates']}"
    )
