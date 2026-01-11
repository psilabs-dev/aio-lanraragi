"""
Metadata editor page (edit.js) integration tests.
"""

import asyncio
import logging
from pathlib import Path
import sys
import tempfile
from typing import AsyncGenerator, Dict, Generator

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
@pytest.mark.failing
async def test_edit_paste_prepends_pending_text_to_first_tag(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that typing partial text then pasting prepends pending text to pasted tag.
    Expected: type "art" + paste "ist:name" = "artist:name"
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        save_path = create_archive_file(tmpdir, "paste-test", num_pages=3)

        response, error = await upload_archive(
            lrr_client, save_path, save_path.name, semaphore,
            title="Paste Test Archive", tags=""
        )
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        archive_id = response.arcid
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context()
        await context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = await context.new_page()

        # Login (Edit page requires authentication)
        login_url = f"{lrr_client.lrr_base_url}/login"
        await page.goto(login_url, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")

        password_input = page.locator("#pw_field")
        await password_input.fill("kamimamita")
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("networkidle")

        # Navigate to Edit page
        edit_url = f"{lrr_client.lrr_base_url}/edit?id={archive_id}"
        await page.goto(edit_url, timeout=60000)
        await page.wait_for_load_state("domcontentloaded")

        # Dismiss any popups
        if "New Version Release Notes" in await page.content():
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

        # Wait for tagger to initialize
        await page.wait_for_function("Edit.tagInput !== null", timeout=10000)

        tagger_new = page.locator(".tagger-new")
        if await tagger_new.count() == 0:
            pytest.skip("Tagger library not initialized. The handlePaste bug only applies when tagger is active.")

        # Focus and type partial text
        await tagger_new.click()
        tagger_input = page.locator(".tagger-new input")
        await tagger_input.wait_for(state="visible", timeout=5000)
        await tagger_input.type("art", delay=50)

        input_value = await tagger_input.input_value()
        assert input_value == "art", f"Expected 'art' before paste, got '{input_value}'"

        # Paste using native clipboard API
        await page.evaluate("navigator.clipboard.writeText('ist:name')")
        modifier = "Meta" if sys.platform == "darwin" else "Control"
        await page.keyboard.press(f"{modifier}+v")
        await asyncio.sleep(0.5)

        # Get resulting tags using locators
        tag_labels = page.locator(".tagger.wrap ul li:not(.tagger-new) .label")
        tag_count = await tag_labels.count()
        tag_texts = []
        for i in range(tag_count):
            text = await tag_labels.nth(i).text_content()
            if text and not text.startswith("date_added:"):
                tag_texts.append(text)

        LOGGER.debug(f"Tags after paste: {tag_texts}")

        expected_tags = {"artist:name"}
        actual_tags = set(tag_texts)
        assert actual_tags == expected_tags, (
            f"Expected exactly {expected_tags} after typing 'art' and pasting 'ist:name'. "
            f"Got {actual_tags}."
        )

        await browser.close()
    # <<<<< UI STAGE <<<<<

    expect_no_error_logs(environment)
