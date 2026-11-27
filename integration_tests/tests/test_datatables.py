"""
Tests for datatables-related behavior, including reader-to-index transitions
and cross-DT-page archive navigation.
"""

import asyncio
import logging
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive
from aio_lanraragi_tests.utils.concurrency import get_bounded_sem
from aio_lanraragi_tests.utils.playwright import (
    assert_no_spinner,
    read_rendered_entries,
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
async def test_index_reader_navigation(
        lrr_client: LRRClient, semaphore: asyncio.Semaphore,
        environment: AbstractLRRDeploymentContext,
        playwright_context,
):
    """
    Test navigation and browser history between the index and the reader.

    1. Set pagesize=3, upload 5 archives (3 pages each).
    2. Open the index, verify index page 1 shows archives 1-3.
    3. Search for "archive" with the search bar, verify index page 1 still shows archives 1-3.
    4. Reload the search results, press back, verify arrival at the unfiltered index; press forward,
       verify the search results.
    5. Open archive 3; press back, reload the search results, press forward, verify archive 3 in the reader.
    6. Navigate forward via ArrowRight into archive 4, the first archive on index page 2.
    7. Click return-to-index icon, verify return to index.
    8. Expect no server error logs.
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
        for i in range(5):
            title = f"archive {i+1}"
            archive_path = create_archive_file(Path(tmpdir), f"archive_{i+1}", num_pages=3)
            response, error = await upload_archive(
                lrr_client, archive_path, archive_path.name, semaphore,
                title=title, tags="",
            )
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            title_to_arcid[title] = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright_context(lrr_client) as pcm:
        page = pcm.page

        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        await pcm.close_release_notes()
        await assert_no_spinner(page)
        expected_page_1 = ["archive 1", "archive 2", "archive 3"]
        entries = await read_rendered_entries(page, 3, expected_page_1)
        page_1_titles = []
        for title, _ in entries:
            page_1_titles.append(title)
        assert page_1_titles == expected_page_1, f"Expected index page 1 to show {expected_page_1}, got {page_1_titles}"
        await pcm.assert_ok()
        pcm.clear()

        await page.locator("#search-input").fill("archive")
        await page.get_by_role("button", name="Apply Filter").click()
        await page.wait_for_url(lambda url: "q=archive" in url)
        await page.wait_for_load_state("networkidle")
        await assert_no_spinner(page)
        entries = await read_rendered_entries(page, 3, expected_page_1)
        page_1_titles = []
        for title, _ in entries:
            page_1_titles.append(title)
        assert page_1_titles == expected_page_1, f"Expected search results page 1 to show {expected_page_1}, got {page_1_titles}"
        await pcm.assert_ok()
        pcm.clear()

        # verify reloading the search results does not add a history entry
        await page.reload(wait_until="networkidle")
        await page.go_back(wait_until="networkidle")
        assert "/reader" not in page.url and "q=archive" not in page.url, (
            f"Expected browser back after reload to reach the unfiltered index, got {page.url}"
        )
        await page.go_forward(wait_until="networkidle")
        assert "q=archive" in page.url, f"Expected browser forward to reach the search results, got {page.url}"
        await assert_no_spinner(page)
        entries = await read_rendered_entries(page, 3, expected_page_1)
        page_1_titles = []
        for title, _ in entries:
            page_1_titles.append(title)
        assert page_1_titles == expected_page_1, f"Expected search results page 1 to show {expected_page_1}, got {page_1_titles}"
        await pcm.assert_ok()
        pcm.clear()

        await pcm.close_release_notes()
        LOGGER.debug("Opening archive 3 from search results page 1.")
        await page.locator("#thumbs_container a", has_text="archive 3").first.click()
        await page.wait_for_load_state("networkidle")
        await assert_no_spinner(page)
        assert f"id={title_to_arcid['archive 3']}" in page.url, f"Expected archive 3 (id={title_to_arcid['archive 3']}) in the reader, got {page.url}"

        # verify forward history to the reader survives a search results reload
        await page.go_back(wait_until="networkidle")
        assert "q=archive" in page.url, f"Expected browser back to reach the search results, got {page.url}"
        await page.reload(wait_until="networkidle")
        await page.go_forward(wait_until="networkidle")
        assert f"id={title_to_arcid['archive 3']}" in page.url, (
            f"Expected browser forward to return to archive 3 (id={title_to_arcid['archive 3']}) after reload, got {page.url}"
        )
        await assert_no_spinner(page)
        await pcm.assert_ok()
        pcm.clear()

        # 3 presses move from page 1 of archive 3 into archive 4
        LOGGER.debug("Navigating forward across the index page boundary.")
        for _ in range(3):
            await page.keyboard.press("ArrowRight")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)
        assert f"id={title_to_arcid['archive 4']}" in page.url, (
            f"Expected to land on archive 4 (id={title_to_arcid['archive 4']}), the first on index page 2, got {page.url}"
        )
        await pcm.assert_ok()
        pcm.clear()

        LOGGER.debug("Clicking return-to-index icon.")
        await page.locator("#return-to-index").click()
        await page.wait_for_load_state("networkidle")
        assert "/reader" not in page.url, f"Expected index page after icon click, got {page.url}"

        await pcm.assert_ok()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)
