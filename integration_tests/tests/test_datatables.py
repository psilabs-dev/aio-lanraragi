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
from lanraragi.models.search import SearchArchiveIndexRequest

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive
from aio_lanraragi_tests.utils.concurrency import get_bounded_sem
from aio_lanraragi_tests.utils.playwright import (
    PlaywrightTestContextManager,
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
    # expected landing archive is the first on index page 2 (4th in search order)
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
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

        # Navigate to index page.
        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        await pcm.close_release_notes()

        # Collect the order of the 3 archives on index page 1.
        await assert_no_spinner(page)
        dt_entries = await read_rendered_entries(page, 3)
        assert len(dt_entries) == 3, f"Expected 3 archives rendered on index page 1, got {len(dt_entries)}"
        dt_titles = []
        dt_arcids = []
        for title, arcid in dt_entries:
            dt_titles.append(title)
            dt_arcids.append(arcid)
        LOGGER.debug(f"Index page 1 order: {list(zip(dt_titles, dt_arcids))}")
        assert dt_arcids[2] == full_order[2], "Index order disagrees with search API order at the page boundary"
        pcm.clear()

        # Open last archive on index page 1 (3rd of 3).
        LOGGER.debug(f"Opening last archive on index page 1: {dt_titles[2]}")
        await page.locator("#thumbs_container a", has_text=dt_titles[2]).first.click()
        await page.wait_for_load_state("networkidle")
        await assert_no_spinner(page)
        assert f"id={dt_arcids[2]}" in page.url

        # Navigate forward through 3 pages to cross into index page 2.
        LOGGER.debug("Navigating forward across the index page boundary.")
        pcm.clear()
        for i in range(3):
            await page.keyboard.press("ArrowRight")
            await page.wait_for_load_state("networkidle")
            await assert_no_spinner(page)
            await page.wait_for_timeout(500)

        # After 3 ArrowRight presses from page 1 of archive 3 (last on index page 1),
        # we should have crossed into the next archive (first on index page 2).
        cross_dt_url = page.url
        LOGGER.debug(f"After crossing the index page boundary: {cross_dt_url}")
        assert f"id={dt_arcids[2]}" not in cross_dt_url, "Expected to have crossed the index page boundary"
        assert f"id={expected_next_arcid}" in cross_dt_url, (
            f"Expected to land on index page 2's first archive {expected_next_arcid}, got {cross_dt_url}"
        )

        # Return-to-index icon should navigate to index.
        LOGGER.info("Clicking return-to-index icon.")
        pcm.clear()
        await page.locator("#return-to-index").click()
        await page.wait_for_load_state("networkidle")
        index_url = page.url
        LOGGER.info(f"After return-to-index: {index_url}")
        assert "/reader" not in index_url, f"Expected index page after icon click, got {index_url}"

        await pcm.assert_ok()
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
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

        # Navigate to index with search filter.
        await page.goto(f"{lrr_client.lrr_base_url}/?q=History")
        await page.wait_for_load_state("networkidle")
        await pcm.close_release_notes()

        # Find the archive as rendered in the index results.
        await assert_no_spinner(page)
        dt_entries = await read_rendered_entries(page, 1)
        assert len(dt_entries) == 1, f"Expected 1 archive rendered, got {len(dt_entries)}"
        dt_title = dt_entries[0][0]
        pcm.clear()

        # Click archive to enter reader.
        await page.locator("#thumbs_container a", has_text=dt_title).first.click()
        await page.wait_for_load_state("networkidle")
        assert "/reader" in page.url
        LOGGER.debug(f"Entered reader: {page.url}")

        pcm.clear()
        await page.go_back(wait_until="networkidle")
        LOGGER.debug(f"After back: {page.url}")
        assert "/reader" not in page.url

        pcm.clear()
        await page.reload(wait_until="networkidle")
        LOGGER.debug(f"After reload: {page.url}")

        # Browser forward should return to reader.
        pcm.clear()
        await page.go_forward(wait_until="networkidle")
        forward_url = page.url
        LOGGER.debug(f"After forward: {forward_url}")
        assert "/reader" in forward_url, f"Expected browser forward to return to the reader after reload, got {forward_url}"

        await pcm.assert_ok()
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
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page

        # Navigate to login page as anchor.
        await page.goto(f"{lrr_client.lrr_base_url}/login")
        await page.wait_for_load_state("networkidle")
        pcm.clear()

        # Navigate to index with search filter.
        await page.goto(f"{lrr_client.lrr_base_url}/?q=test")
        await page.wait_for_load_state("networkidle")
        await pcm.close_release_notes()
        pcm.clear()

        await page.reload(wait_until="networkidle")
        LOGGER.debug(f"After reload: {page.url}")
        pcm.clear()

        # Back twice should reach the login page.
        await page.go_back(wait_until="networkidle")
        LOGGER.debug(f"After first back: {page.url}")
        await page.go_back(wait_until="networkidle")
        back_url = page.url
        LOGGER.debug(f"After second back: {back_url}")
        assert "/login" in back_url, f"Expected two browser backs after reload to reach the login page, got {back_url}"

        await pcm.assert_ok()
    # <<<<< UI STAGE <<<<<
