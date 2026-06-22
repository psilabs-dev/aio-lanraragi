"""
GitHub update-check rate-limit reproduction for the api.github.com console-error guard.

LRR's index page runs index.js checkVersion()/fetchChangelog() on load, which make an
unauthenticated browser fetch to https://api.github.com/repos/difegue/lanraragi/releases/latest.
GitHub's unauthenticated REST limit is 60 requests/hour per source IP. This test forces that limit
by loading the index page in many fresh browser contexts (each with a cold cache, so the update
check makes a real request every time), then confirms assert_console_logs_ok tolerates the resulting
GitHub noise instead of failing the test.

Caveat: from an ordinary (non-Actions) egress IP, GitHub returns CORS headers even when rate-limited,
so the throttled response is a handled 403 (console.warn, not an error) and the error-level CORS
console message does not appear -- the test then passes without exercising the guard. The error
variant (a rate-limited response missing Access-Control-Allow-Origin, which Chromium reports as a CORS
console error) is GitHub-Actions-egress specific; run this on a GitHub-hosted runner to exercise the
guard against the real shape.
"""

import contextlib
import logging

import playwright.async_api
import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.utils.playwright import assert_console_logs_ok

LOGGER = logging.getLogger(__name__)

# GitHub's unauthenticated REST limit is 60 requests/hour per source IP. Loading the index page in
# this many fresh contexts (cold cache, so the update check makes a real request each time) crosses
# that threshold regardless of the runner IP's starting quota.
_INDEX_LOADS = 65


@pytest.mark.asyncio
@pytest.mark.playwright
async def test_github_update_check_ratelimit_console_tolerated(lrr_client: LRRClient) -> None:
    """
    Exhaust GitHub's unauthenticated rate limit via the index update-check and confirm the
    api.github.com console-error guard tolerates the result.

    1. Confirm the server is reachable.
    2. Load the index page in 65 fresh browser contexts, aggregating console/response events, so the
       index.js GitHub update check is forced past the 60/hr unauthenticated limit.
    3. Log how many GitHub 4xx responses and error-level GitHub console events were observed.
    4. Assert assert_console_logs_ok does not raise on the aggregated console events.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    # <<<<< TEST CONNECTION STAGE <<<<<

    all_console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
    all_responses: list[playwright.async_api._generated.Response] = []

    # >>>>> UI STAGE >>>>>
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for _ in range(_INDEX_LOADS):
                bc = await browser.new_context()
                try:
                    page = await bc.new_page()
                    page.on("console", lambda console: all_console_evts.append(console))
                    page.on("response", lambda response: all_responses.append(response))

                    await page.goto(lrr_client.lrr_base_url)
                    await page.wait_for_load_state("domcontentloaded")
                    with contextlib.suppress(playwright.async_api.TimeoutError):
                        await page.wait_for_load_state("networkidle", timeout=15000)

                    if "New Version Release Notes" in await page.content():
                        await page.keyboard.press("Escape")
                finally:
                    await bc.close()
        finally:
            await browser.close()
    # <<<<< UI STAGE <<<<<

    github_4xx = [r for r in all_responses if "api.github.com" in r.url and r.status >= 400]
    github_console_errors = [e for e in all_console_evts if e.type == "error" and "api.github.com" in e.text]
    LOGGER.info(
        f"GitHub update-check after {_INDEX_LOADS} index loads: {len(github_4xx)} throttled (4xx) "
        f"responses, {len(github_console_errors)} error-level GitHub console events."
    )
    if not github_4xx and not github_console_errors:
        LOGGER.warning(
            "No GitHub throttling observed: the runner IP was under quota this run, so the guard was "
            "not exercised against a real throttled response."
        )

    # The guard must tolerate whatever GitHub noise appeared (handled 403 console.warn, or an
    # error-level CORS console message on Actions egress) rather than failing this LRR-focused test.
    await assert_console_logs_ok(all_console_evts, lrr_client.lrr_base_url)
