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
    Test plugin install / uninstall / reinstall through the Manage tab batch UI.

    1. Create registry, refresh index via API.
    2. Manage tab: toggle sample-metadata checkbox, click Apply, verify install.
    3. Managed badge renders in the Configure tab.
    4. Manage tab: toggle checkbox off, click Apply, verify uninstall.
    5. Card absent from Configure tab, namespace absent from API.
    6. Refresh registry, verify sample-metadata available for reinstall.
    7. Reinstall via Apply, verify managed provenance.
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
            await page.goto(f"{lrr_client.lrr_base_url}/config/plugins#tab-manage")
            await page.wait_for_load_state("networkidle")

            if "login" in page.url.lower():
                await page.fill("#pw_field", DEFAULT_LRR_PASSWORD)
                await page.click("input[type='submit'][value='Login']")
                await page.wait_for_load_state("networkidle")
            assert "plugins" in page.url, f"Expected plugins page, got: {page.url}"
            responses.clear()
            console_evts.clear()
            # <<<<< LOGIN <<<<<

            # >>>>> EXPAND MANAGE SECTION AND REFRESH AVAILABLE >>>>>
            await page.locator("#manage-section-metadata .collapsible-title", has_text="Metadata Plugins").click()
            await page.locator("#registry-refresh-btn").click()
            await page.locator(".manage-install-cb[data-namespace='sample-metadata']").wait_for(state="attached")
            # <<<<< EXPAND MANAGE SECTION AND REFRESH AVAILABLE <<<<<

            # >>>>> INSTALL VIA BATCH APPLY >>>>>
            # Check the install checkbox — this marks the plugin for install
            # without firing the request. The batch fires on #manage-apply-btn,
            # which first opens a SweetAlert confirm popup (plugins.js:165).
            await page.locator(".manage-install-cb[data-namespace='sample-metadata']").check()
            await page.locator("#manage-apply-btn").click()
            await page.locator(".swal2-confirm").wait_for(state="visible")
            async with page.expect_response("**/api/plugins/install") as response_info:
                await page.locator(".swal2-confirm").click()
            install_response = await response_info.value
            assert install_response.ok, f"Install API failed: {install_response.status}"

            # Apply reloads the page (plugins.js:553-554); wait for it to settle.
            await page.wait_for_load_state("networkidle")
            # <<<<< INSTALL VIA BATCH APPLY <<<<<

            # >>>>> VERIFY INSTALLED >>>>>
            badge = page.locator(".plugin-card[data-namespace='sample-metadata'] .plugin-badge")
            await badge.wait_for(state="attached")
            assert await badge.text_content() == "managed", \
                f"Expected 'managed' badge after install, got: {await badge.text_content()}"
            # <<<<< VERIFY INSTALLED <<<<<

            # >>>>> UNINSTALL VIA BATCH APPLY >>>>>
            # Post-reload, the Manage tab reloads available plugins automatically
            # (plugins.js:596-598 when #tab-manage hash is active on page load).
            # Re-expand the section in case it collapsed, then toggle the cb off.
            await page.locator("#manage-section-metadata .collapsible-title", has_text="Metadata Plugins").click()
            await page.locator(".manage-install-cb[data-namespace='sample-metadata']").wait_for(state="attached")
            await page.locator(".manage-install-cb[data-namespace='sample-metadata']").uncheck()

            await page.locator("#manage-apply-btn").click()
            await page.locator(".swal2-confirm").wait_for(state="visible")
            async with page.expect_response("**/api/plugins/installed/sample-metadata") as response_info:
                await page.locator(".swal2-confirm").click()
            uninstall_response = await response_info.value
            assert uninstall_response.ok, f"Uninstall API failed: {uninstall_response.status}"

            await page.wait_for_load_state("networkidle")
            # <<<<< UNINSTALL VIA BATCH APPLY <<<<<

            # >>>>> VERIFY REMOVED >>>>>
            card_count = await page.locator(".plugin-card[data-namespace='sample-metadata']").count()
            assert card_count == 0, f"sample-metadata still in DOM after uninstall (count: {card_count})"

            response, error = await lrr_client.misc_api.get_available_plugins(
                GetAvailablePluginsRequest(type="metadata")
            )
            assert not error, f"Failed to list plugins after uninstall (status {error.status}): {error.error}"
            namespaces = {p.namespace for p in response.plugins}
            assert "sample-metadata" not in namespaces, f"Plugin still in API after uninstall: {namespaces}"
            # <<<<< VERIFY REMOVED <<<<<

            # >>>>> REINSTALL >>>>>
            await page.locator("#manage-section-metadata .collapsible-title", has_text="Metadata Plugins").click()
            await page.locator(".manage-install-cb[data-namespace='sample-metadata']").wait_for(state="attached")
            await page.locator(".manage-install-cb[data-namespace='sample-metadata']").check()

            await page.locator("#manage-apply-btn").click()
            await page.locator(".swal2-confirm").wait_for(state="visible")
            async with page.expect_response("**/api/plugins/install") as response_info:
                await page.locator(".swal2-confirm").click()
            reinstall_response = await response_info.value
            assert reinstall_response.ok, f"Reinstall API failed: {reinstall_response.status}"

            await page.wait_for_load_state("networkidle")

            badge_after = page.locator(".plugin-card[data-namespace='sample-metadata'] .plugin-badge")
            await badge_after.wait_for(state="attached")
            assert await badge_after.text_content() == "managed", \
                f"Expected 'managed' after reinstall, got: {await badge_after.text_content()}"
            # <<<<< REINSTALL <<<<<

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()

    expect_no_error_logs(environment, LOGGER)
