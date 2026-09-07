"""
Category editor UI integration tests for the LANraragi server.
Covers behavior of the category editor at /config/categories.
"""

import logging

import playwright.async_api
import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.category import (
    CreateCategoryRequest,
    GetCategoryRequest,
    UpdateCategoryRequest,
)

from aio_lanraragi_tests.common import DEFAULT_LRR_PASSWORD
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
    assert_toasts_ok,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.failing
async def test_category_editor(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
) -> None:
    """
    Test that the category editor renders a category's stored state and edits it in place.

    The editor saves every field on any change, so an edit to one field writes the
    rendered state of all the others back.

    1. Create a category and pin it via the API.
    2. Open the editor and select it.
       - Expect the pin checkbox to be checked.
    3. Change only the name.
       - Expect the new name to persist.
       - Expect the category to still be pinned.
    4. Expect no HTTP errors, no console errors, no server error logs.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> CREATE PINNED CATEGORY >>>>>
    response, error = await lrr_client.category_api.create_category(CreateCategoryRequest(name="pin-rename"))
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    category_id = response.category_id

    _, error = await lrr_client.category_api.update_category(
        UpdateCategoryRequest(category_id=category_id, pinned=True)
    )
    assert not error, f"Failed to pin category (status {error.status}): {error.error}"

    response, error = await lrr_client.category_api.get_category(GetCategoryRequest(category_id=category_id))
    assert not error, f"Failed to read back category (status {error.status}): {error.error}"
    assert response.pinned, f"Category {category_id} was not pinned"

    renamed = "pin-renamed"
    # <<<<< CREATE PINNED CATEGORY <<<<<

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

            # login; category management requires a logged-in user
            await page.goto(f"{lrr_client.lrr_base_url}/login", timeout=60000)
            await page.wait_for_load_state("networkidle")
            await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
            await page.get_by_role("button", name="Login").click()
            await page.wait_for_load_state("networkidle")
            responses.clear()
            console_evts.clear()

            await page.goto(f"{lrr_client.lrr_base_url}/config/categories", timeout=60000)
            await page.wait_for_load_state("networkidle")

            await page.locator(f"#category option[value='{category_id}']").wait_for(state="attached", timeout=5000)
            await page.select_option("#category", category_id)
            await page.wait_for_timeout(300)

            # >>>>> READ PIN CHECKBOX >>>>>
            checkbox = page.locator("#pinned")
            await checkbox.wait_for(state="attached", timeout=5000)
            checkbox_state = await checkbox.is_checked()
            # <<<<< READ PIN CHECKBOX <<<<<

            # >>>>> RENAME >>>>>
            await page.locator("#catname").fill(renamed)
            await page.locator("#catname").blur()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)
            # <<<<< RENAME <<<<<

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
            await assert_toasts_ok(page)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< UI STAGE <<<<<

    # >>>>> VERIFY STAGE >>>>>
    response, error = await lrr_client.category_api.get_category(GetCategoryRequest(category_id=category_id))
    assert not error, f"Failed to read back category (status {error.status}): {error.error}"
    assert response.name == renamed, f"Expected name {renamed!r} after rename, got {response.name!r}"
    assert response.pinned, (
        f"Category {category_id} was unpinned by a rename; editor rendered pin checkbox as {checkbox_state}"
    )
    assert checkbox_state, f"Expected pin checkbox checked for pinned category {category_id}"
    # <<<<< VERIFY STAGE <<<<<

    expect_no_error_logs(environment, LOGGER)
