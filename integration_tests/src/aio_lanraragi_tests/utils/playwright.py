import logging
from urllib.parse import urlparse

import playwright.async_api._generated
from lanraragi.clients.client import LRRClient

LOGGER = logging.getLogger(__name__)

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

async def assert_console_logs_ok(
        console_evts: list[playwright.async_api._generated.ConsoleMessage],
        lrr_base_url: str
):
    """
    Assert that all LRR console logs captured during a Playwright browser session were not errors.
    """

    for evt in console_evts:
        if (url := evt.location.get("url")) and not url.startswith(lrr_base_url):
            LOGGER.debug(f"Skipping non-LRR console log: {url}")
            continue
        LOGGER.info(f"Console: {evt.text}")

        assert evt.type != "error", f"Console logged at error level: {evt.text}"

async def assert_no_spinner(page: playwright.async_api.Page, timeout_ms: int = 3000):
    """Assert that the reader loading spinner is gone within timeout_ms."""
    await page.wait_for_function(
        """() => !document.querySelector('#i3.loading')""",
        timeout=timeout_ms,
    )

async def get_image_bytes_from_responses(
        responses: list[playwright.async_api._generated.Response],
        img_src: str,
) -> bytes:
    """
    Find the captured browser response matching a given image src URL and return its body bytes.

    Searches the captured responses list for a successful GET matching the exact URL.
    Raises AssertionError if no matching response is found.
    """
    for resp in responses:
        if resp.request.method == "GET" and resp.url == img_src and resp.status == 200:
            return await resp.body()
    raise AssertionError(f"Could not find browser response for img src={img_src!r}")
