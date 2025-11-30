"""
Encode/decode-related tests.
"""

from aio_lanraragi_tests.common import DEFAULT_LRR_PASSWORD, LRR_INDEX_TITLE, LRR_LOGIN_TITLE, compute_archive_id
import numpy as np
import asyncio
import logging
from pathlib import Path
import tempfile
from typing import Dict, Generator, List

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.factory import generate_deployment
import pytest
from playwright import async_api as playwright
import pytest_asyncio

from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import DeleteArchiveRequest, GetArchiveMetadataRequest
from aio_lanraragi_tests.helpers import expect_no_error_logs, get_bounded_sem, save_archives, upload_archive

LOGGER = logging.getLogger(__name__)
ENABLE_SYNC_FALLBACK = False # for debugging.

def basenames() -> List[str]:
    return [
        "test-☆",
        "test-e\u0301",     # decomposed
        "test-é",           # composed
        "emoji-🚀",
        "cjk-中文",
        "!Γÿå",
    ]

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)

    # configure environments to session
    environments: Dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

    yield environment
    environment.teardown(remove_data=True)

@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield get_bounded_sem()

@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) ->  Generator[LRRClient, None, None]:
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()

@pytest.fixture
def npgenerator(request: pytest.FixtureRequest) -> Generator[np.random.Generator, None, None]:
    seed: int = int(request.config.getoption("npseed"))
    generator = np.random.default_rng(seed)
    yield generator

@pytest.mark.flaky(reruns=2, condition=False, only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.parametrize("basename", basenames())
async def test_upload_page_filename_consistency(
    basename: str, lrr_client: LRRClient, environment: AbstractLRRDeploymentContext, 
    npgenerator: np.random.Generator, semaphore: asyncio.BoundedSemaphore,
):
    """
    Check that the following are equal:

    - the filename of an archive uploaded via the upload page,
    - the filename of an archive uploaded via the upload API.
    """

    num_archives = 1
    with tempfile.TemporaryDirectory() as tmpdir:
        write_responses = save_archives(num_archives, Path(tmpdir), npgenerator)

        original_path = write_responses[0].save_path
        target_path = original_path.parent / f"{basename}{original_path.suffix}"
        original_path.rename(target_path)
        arcid = compute_archive_id(target_path)

        # UI upload via Playwright
        async with playwright.async_playwright() as p:
            LOGGER.info("Opening browser")
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_INDEX_TITLE
            
            # enter admin portal
            # exit overlay
            LOGGER.info("Checking new release notes")
            if "New Version Release Notes" in await page.content():
                LOGGER.info("Closing new releases overlay.")
                await page.keyboard.press("Escape")

            LOGGER.info("Checking Admin Login page")
            assert "Admin Login" in await page.content(), "Admin Login not found!"

            LOGGER.info("Click Admin Login button")
            await page.get_by_role("link", name="Admin Login").click()
            assert await page.title() == LRR_LOGIN_TITLE

            LOGGER.info("Entering default password")
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_INDEX_TITLE

            # do the upload
            await page.goto(f"{lrr_client.lrr_base_url}/upload")
            LOGGER.info(f"Uploading archive {target_path}")
            await page.set_input_files("#fileupload", str(target_path))

            await asyncio.sleep(5)
            await browser.close()

        # Expect archives.
        LOGGER.info("Validating upload counts.")
        response, error = await lrr_client.archive_api.get_all_archives()
        assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
        assert len(response.data) == num_archives, "Uploaded archive not captured!"
        assert response.data[0].arcid == arcid, "Archive IDs not equal!"

        # Get filename metadata from archive uploaded through upload page.
        LOGGER.info(f"Getting archive metadata for {arcid}")
        response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
        assert not error, f"Failed to get archive metadata (status {error.status}): {error.error}"
        upload_page_filename = response.filename + '.' + response.extension

        # Delete archive (and re-upload via the Upload API)
        LOGGER.info("Deleting archive")
        response, error = await lrr_client.archive_api.delete_archive(DeleteArchiveRequest(arcid=arcid))
        assert not error, f"Failed to delete archive {arcid} (status {error.status}): {error.error}"

        # Expect no archives.
        LOGGER.info("Validating upload counts.")
        response, error = await lrr_client.archive_api.get_all_archives()
        assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
        assert len(response.data) == 0, "Archives not cleared!"

        # API upload
        response, error = await upload_archive(lrr_client, target_path, target_path.name, semaphore)
        assert not error, f"Upload failed (status {error.status}): {error.error}"

        # Expect archives.
        LOGGER.info("Validating upload counts.")
        response, error = await lrr_client.archive_api.get_all_archives()
        assert not error, f"Failed to get archive data (status {error.status}): {error.error}"
        assert len(response.data) == num_archives, "Uploaded archive not captured!"
        assert response.data[0].arcid == arcid, "Archive IDs not equal!"

        # Get filename metadata from archive uploaded through upload page.
        LOGGER.info(f"Getting archive metadata for {arcid}")
        response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
        assert not error, f"Failed to get archive metadata (status {error.status}): {error.error}"
        upload_api_filename = response.filename + '.' + response.extension

        # Check the upload page filename equals upload api filename.
        if upload_page_filename != upload_api_filename:
            diff_index = next((i for i, (x, y) in enumerate(zip(upload_page_filename, upload_api_filename)) if x != y), min(len(upload_page_filename), len(upload_api_filename)))
            upload_page_chars = " ".join(f"U+{ord(c):04X}" for c in upload_page_filename)
            upload_api_chars = " ".join(f"U+{ord(c):04X}" for c in upload_api_filename)
            original_filename_chars = " ".join(f"U+{ord(c):04X}" for c in basename)
            pytest.fail(
                "The two strings passed do not match.\n"
                f"Filename (upload page):   {upload_page_filename!r}\n"
                f"Filename (upload API):    {upload_api_filename!r}\n"
                f"Filename (original):      {basename}\n"
                f"first_diff_index:         {diff_index}\n"
                f"chars (upload page):      {upload_page_chars}\n"
                f"chars (upload API):       {upload_api_chars}\n"
                f"chars (original):         {original_filename_chars}"
            )

    expect_no_error_logs(environment)
