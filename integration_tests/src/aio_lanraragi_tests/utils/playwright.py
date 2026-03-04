import logging
import re
from urllib.parse import urlparse

import playwright.async_api._generated
from lanraragi.clients.client import LRRClient

LOGGER = logging.getLogger(__name__)
IGNORED_EXTERNAL_CONSOLE_ERROR_PATTERN = r"https://api\.github\.com/repos/difegue/lanraragi/releases/latest|Github API rate limit exceeded|Failed to load resource: the server responded with a status of 403"

async def assert_browser_responses_ok(responses: list[playwright.async_api._generated.Response], lrr_client: LRRClient, logger: logging.Logger=LOGGER):
    """
    Assert that all responses captured during a Playwright browser session were normal. This means:

    - Any LRR-side URL returned a 2xx, 3xx, or 401 (unauthenticated) status code.
    """
    lrr_hostname = urlparse(lrr_client.lrr_host).hostname
    hostnames = {lrr_hostname} if lrr_hostname != '127.0.0.1' else {'127.0.0.1', 'localhost'}

    for response in responses:
        url = response.url
        status = response.status

        parsed = urlparse(url)
        hostname = parsed.hostname

        # Check that all LRR requests were handled successfully.
        # if non-LRR, then throw warning if not successful (e.g. Github API rate limits).
        if hostname in hostnames:
            logger.debug(f"Request {url} (status {status})")

            if status < 400 or status == 401:
                continue

            # get the error message.
            text = await response.text()
            raise AssertionError(f"Status {status} with {response.request.method} {response.url}: {text}")
        elif status >= 400:
            logger.warning(f"Status {status} with {response.request.method} {response.url}")

async def assert_console_logs_ok(console_evts: list[playwright.async_api._generated.ConsoleMessage]):
    """
    Assert that all console logs captured during a Playwright browser session were not errors.
    """

    for evt in console_evts:
        if evt.type != "error":
            continue

        text = evt.text or ""
        if re.search(IGNORED_EXTERNAL_CONSOLE_ERROR_PATTERN, text):
            LOGGER.warning(f"Ignoring known external console error: {text}")
            continue

        raise AssertionError(f"Console logged at error level: {text}")

async def assert_no_spinner(page: playwright.async_api.Page, timeout_ms: int = 3000):
    """Assert that the reader loading spinner is gone within timeout_ms."""
    await page.wait_for_function(
        """() => !document.querySelector('#i3.loading')""",
        timeout=timeout_ms,
    )
