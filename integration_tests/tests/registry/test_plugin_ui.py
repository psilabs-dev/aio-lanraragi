"""
Plugin registry Playwright UI integration tests.
"""

import logging

import playwright.async_api
import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
)

from aio_lanraragi_tests.common import DEFAULT_LRR_PASSWORD
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_uninstall_ui(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test plugin install, enable, uninstall, and reinstall through the UI.

    1. Create registry, refresh index via API.
    2. Navigate to plugin page, install sample-metadata from registry.
    3. Move sample-metadata to enabled pool, save configuration.
    4. Uninstall sample-metadata, verify absent from page and API.
    5. Refresh registry, verify sample-metadata available for reinstall.
    6. Reinstall, verify managed provenance.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await bc.new_page()

            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # >>>>> LOGIN >>>>>
            await page.goto(f"{lrr_client.lrr_base_url}/config/plugins")
            await page.wait_for_load_state("networkidle")

            if "login" in page.url.lower():
                await page.fill("#pw_field", DEFAULT_LRR_PASSWORD)
                await page.click("input[type='submit'][value='Login']")
                await page.wait_for_load_state("networkidle")
            assert "plugins" in page.url, f"Expected plugins page, got: {page.url}"
            responses.clear()
            console_evts.clear()
            # <<<<< LOGIN <<<<<

            # >>>>> INSTALL >>>>>
            # Expand the Metadata Plugins collapsible (hidden by allcollapsible on load)
            await page.locator(".collapsible-title", has_text="Metadata Plugins").click()
            await page.wait_for_timeout(500)

            await page.locator("#registry-refresh-btn").click()
            await page.wait_for_load_state("networkidle")

            sample_metadata_row = page.locator(".registry-plugin-row").filter(
                has=page.locator("h2", has_text="Sample Metadata")
            )
            async with page.expect_response("**/api/plugins/install") as response_info:
                await sample_metadata_row.locator("input[type='button']").click()
            install_response = await response_info.value
            assert install_response.ok, f"Install API failed: {install_response.status}"
            # <<<<< INSTALL <<<<<

            # >>>>> VERIFY INSTALLED >>>>>
            badge = page.locator(".plugin-card[data-namespace='sample-metadata'] .plugin-badge")
            await page.wait_for_timeout(500)
            assert await badge.text_content() == "managed", f"Expected 'managed' badge after install, got: {await badge.text_content()}"
            # <<<<< VERIFY INSTALLED <<<<<

            # >>>>> ENABLE AND SAVE >>>>>
            # native drag does not trigger SortableJS; move via DOM
            moved = await page.evaluate("""() => {
                const card = document.querySelector('.plugin-card[data-namespace="sample-metadata"]');
                const enabledPool = document.getElementById('metadata-enabled');
                if (!card || !enabledPool) return false;

                const emptyMsg = enabledPool.querySelector('.pool-empty-msg');
                if (emptyMsg) emptyMsg.remove();

                enabledPool.appendChild(card);
                if (typeof Plugins !== 'undefined' && Plugins.renumberEnabled) {
                    Plugins.renumberEnabled();
                }
                return card.closest('#metadata-enabled') !== null;
            }""")
            assert moved, "Failed to move sample-metadata to enabled pool"

            await page.get_by_role("button", name="Save Plugin Configuration").click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            # <<<<< ENABLE AND SAVE <<<<<

            # >>>>> UNINSTALL >>>>>
            await page.locator(".plugin-uninstall-btn[data-namespace='sample-metadata']").click()

            # confirm uninstall dialog; wait for DELETE response then page reload
            await page.wait_for_selector(".swal2-confirm", state="visible")
            async with page.expect_response("**/api/plugins/installed/**") as response_info:
                await page.click(".swal2-confirm")
            uninstall_response = await response_info.value
            assert uninstall_response.ok, f"Uninstall API failed: {uninstall_response.status}"
            await page.wait_for_load_state("networkidle")
            # <<<<< UNINSTALL <<<<<

            # >>>>> VERIFY REMOVED >>>>>
            card_count = await page.locator(".plugin-card[data-namespace='sample-metadata']").count()
            assert card_count == 0, f"sample-metadata still in DOM after uninstall (count: {card_count})"

            # verify via API
            response, error = await lrr_client.misc_api.get_available_plugins(
                GetAvailablePluginsRequest(type="metadata")
            )
            assert not error, f"Failed to list plugins after uninstall (status {error.status}): {error.error}"
            namespaces = {p.namespace for p in response.plugins}
            assert "sample-metadata" not in namespaces, f"Plugin still in API after uninstall: {namespaces}"
            # <<<<< VERIFY REMOVED <<<<<

            # >>>>> REFRESH AND VERIFY AVAILABLE >>>>>
            # Re-expand collapsible (page reloaded after uninstall)
            await page.locator(".collapsible-title", has_text="Metadata Plugins").click()
            await page.wait_for_timeout(500)

            await page.locator("#registry-refresh-btn").click()
            await page.wait_for_load_state("networkidle")

            reinstall_row = page.locator(".registry-plugin-row").filter(
                has=page.locator("h2", has_text="Sample Metadata")
            )
            await reinstall_row.wait_for(state="visible")
            # <<<<< REFRESH AND VERIFY AVAILABLE <<<<<

            # >>>>> REINSTALL >>>>>
            async with page.expect_response("**/api/plugins/install") as response_info:
                await reinstall_row.locator("input[type='button']").click()
            reinstall_response = await response_info.value
            assert reinstall_response.ok, f"Reinstall API failed: {reinstall_response.status}"

            badge_after = page.locator(".plugin-card[data-namespace='sample-metadata'] .plugin-badge")
            await page.wait_for_timeout(500)
            assert await badge_after.text_content() == "managed", f"Expected 'managed' after reinstall, got: {await badge_after.text_content()}"
            # <<<<< REINSTALL <<<<<

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()

    expect_no_error_logs(environment, LOGGER)
