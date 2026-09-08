import contextlib
import logging
import re
import time
from types import TracebackType
from typing import TypeVar, override
from urllib.parse import urlparse

import playwright.async_api._generated
from lanraragi.clients.client import LRRClient

LOGGER = logging.getLogger(__name__)

_PlaywrightTestContextManagerLike = TypeVar('_PlaywrightTestContextManagerLike', bound='PlaywrightTestContextManager')
class PlaywrightTestContextManager(contextlib.AbstractAsyncContextManager):
    """
    Async context manager for all LRR playwright related testing. Manages the following lifecycle:

    ```python
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            failed_requests: list[playwright.async_api._generated.Request] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))
            page.on("requestfailed", lambda request: failed_requests.append(request))
        finally:
            await bc.close()
            await browser.close()
    ```

    User of this context manager gets:

    - `page`: the page with all this tracking enabled by default.
    - `browser_context`: the owning browser context.
    - `assert_ok`: assert everything is OK.
    - `assert_requests_ok`: assert only requests are OK.
    - `assert_http_ok`: assert only browser HTTP responses are OK.
    - `assert_console_ok`: assert only console logs are OK.
    - `assert_toasts_ok`: assert only toasts are OK.
    - `clear`: drop captured traffic, for tests that assert per stage.

    User guide:

    ```python
    async with PlaywrightTestContextManager(lrr_client) as pcm:
        page = pcm.page
        await page.do_stuff()
        # ...
        await pcm.assert_ok()
    ```
    """

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    @logger.setter
    def logger(self, logger: logging.Logger):
        self._logger = logger

    @property
    def page(self) -> playwright.async_api._generated.Page:
        """
        The page under test, with response, console and failed-request tracking attached.
        """
        if self._page is None:
            raise RuntimeError("page is only available inside the context manager.")
        return self._page

    @property
    def browser_context(self) -> playwright.async_api._generated.BrowserContext:
        """
        The owning browser context.
        """
        if self._browser_context is None:
            raise RuntimeError("browser_context is only available inside the context manager.")
        return self._browser_context

    def __init__(
            self,
            lrr_client: LRRClient,
            browser_type: str="chromium",
            logger: logging.Logger=LOGGER,
    ):
        """
        `browser_type` selects the Playwright browser; use the chromium default unless the test
        is browser-specific.
        """
        self.logger = logger
        self._lrr_client: LRRClient = lrr_client
        self._browser_type: str = browser_type

        self._playwright: playwright.async_api._generated.Playwright | None = None
        self._browser: playwright.async_api._generated.Browser | None = None
        self._browser_context: playwright.async_api._generated.BrowserContext | None = None
        self._page: playwright.async_api._generated.Page | None = None

        self.responses: list[playwright.async_api._generated.Response] = []
        self.console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
        self.failed_requests: list[playwright.async_api._generated.Request] = []

    def clear(self) -> None:
        """
        Drop all captured traffic, so a later assertion only covers the stage that follows.
        """
        self.responses.clear()
        self.console_evts.clear()
        self.failed_requests.clear()

    async def assert_requests_ok(self) -> None:
        await assert_no_failed_requests(self.failed_requests, self._lrr_client, logger=self.logger)

    async def assert_http_ok(self) -> None:
        await assert_browser_responses_ok(self.responses, self._lrr_client, logger=self.logger)

    async def assert_console_ok(self) -> None:
        await assert_console_logs_ok(self.console_evts, self._lrr_client.lrr_base_url)

    async def assert_toasts_ok(self) -> None:
        await assert_toasts_ok(self.page)

    async def assert_ok(self) -> None:
        """
        Assert no failed requests, no HTTP errors, no console errors and no error toasts.
        """
        await self.assert_requests_ok()
        await self.assert_http_ok()
        await self.assert_console_ok()
        await self.assert_toasts_ok()

    @override
    async def __aenter__(self: _PlaywrightTestContextManagerLike) -> _PlaywrightTestContextManagerLike:
        self._playwright = await playwright.async_api.async_playwright().start()
        try:
            browser_launcher: playwright.async_api._generated.BrowserType = getattr(self._playwright, self._browser_type)
            self._browser = await browser_launcher.launch()
            self._browser_context = await self._browser.new_context()
            self._page = await self._browser_context.new_page()
        except BaseException:
            await self._teardown()
            raise

        self._page.on("response", lambda response: self.responses.append(response))
        self._page.on("console", lambda console: self.console_evts.append(console))
        self._page.on("requestfailed", lambda request: self.failed_requests.append(request))
        return self

    @override
    async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
    ) -> bool:
        if exc_type:
            self.logger.error(f"Exception occurred: {exc_type.__name__}: {exc_value}")
        await self._teardown()
        return False

    async def _teardown(self) -> None:
        try:
            if self._browser_context:
                await self._browser_context.close()
        finally:
            try:
                if self._browser:
                    await self._browser.close()
            finally:
                if self._playwright:
                    await self._playwright.stop()

async def assert_browser_responses_ok(
        responses: list[playwright.async_api._generated.Response],
        lrr_client: LRRClient,
        logger: logging.Logger=LOGGER
):
    """
    Assert that all responses captured during a Playwright browser session were normal. This means:

    - Any LRR-side URL returned a 2xx, 3xx, or 401 (unauthenticated) status code.
    """
    lrr_hostname: str = urlparse(lrr_client.lrr_host).hostname or ''
    hostnames: set[str] = {'127.0.0.1', 'localhost'} if lrr_hostname == '127.0.0.1' else {lrr_hostname}

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

            # Skip locked resource checks
            if status == 423:
                text = await response.text()
                logger.warning(f"Tolerating transient lock (423) on {response.request.method} {response.url}: {text}")
                continue

            # get the error message.
            text = await response.text()
            raise AssertionError(f"Status {status} with {response.request.method} {response.url}: {text}")
        elif status >= 400:
            logger.warning(f"Status {status} with {response.request.method} {response.url}")

async def assert_no_failed_requests(requests: list[playwright.async_api._generated.Request], lrr_client: LRRClient, logger: logging.Logger=LOGGER):
    """
    Assert that no LRR-side request failed before it received a response. This means:

    - Any LRR-side URL that never reached the server, e.g. net::ERR_CONNECTION_FAILED.

    These never produce a response, so assert_browser_responses_ok cannot see them. Cancelled
    requests (net::ERR_ABORTED) are tolerated.
    """
    lrr_hostname: str = urlparse(lrr_client.lrr_host).hostname or ''
    hostnames: set[str] = {'127.0.0.1', 'localhost'} if lrr_hostname == '127.0.0.1' else {lrr_hostname}

    for request in requests:
        url = request.url
        failure = request.failure

        parsed = urlparse(url)
        hostname = parsed.hostname

        if failure == "net::ERR_ABORTED":
            logger.debug(f"Skipping cancelled request {url}")
            continue

        if hostname in hostnames:
            raise AssertionError(f"Request failed with {failure}: {request.method} {url}")
        logger.warning(f"Request failed with {failure}: {request.method} {url}")

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
        if evt.type == "error" and "api.github.com" in evt.text:
            LOGGER.warning(f"Skipping external GitHub error console log: {evt.text}")
            continue
        LOGGER.info(f"Console: {evt.text}")

        assert evt.type != "error", f"Console logged at error level: {evt.text}"

async def switch_display_mode(page: playwright.async_api._generated.Page, mode: str) -> None:
    """Open the index settings cog menu, pick the requested display mode ('thumbnail' or 'compact'), and close the menu."""
    value = "1" if mode == "thumbnail" else "0"
    await page.locator("#settings-menu").click()
    await page.locator(
        f"li.context-menu-input:has(input[name='context-menu-input-displayMode'][value='{value}'])"
    ).click()
    # the radio item keeps the contextMenu open; dismiss it so subsequent clicks aren't intercepted
    await page.keyboard.press("Escape")
    await page.locator("ul.context-menu-list").wait_for(state="hidden")

async def assert_no_spinner(page: playwright.async_api.Page, timeout_ms: int = 3000):
    """
    Assert that no spinners are active that can indicate something is loading when it shouldn't.
    """
    await page.wait_for_function(
        """() => {
            const readerSpinning = document.querySelector('#i3.loading') !== null;
            const indexProc = document.querySelector('#progress');
            const indexSpinning = indexProc !== null && indexProc.offsetParent !== null;
            return !readerSpinning && !indexSpinning;
        }""",
        timeout=timeout_ms,
    )

async def assert_toasts_ok(page: playwright.async_api.Page):
    """
    Assert that none of LRR toast messages have severity error.
    """
    error_toasts = page.locator(".Toastify__toast--error")
    count = await error_toasts.count()
    for i in range(count):
        text = (await error_toasts.nth(i).inner_text()).strip()
        if "github" in text.lower():
            LOGGER.warning(f"Skipping external GitHub error toast: {text}")
            continue

        raise AssertionError(f"Expected no error toasts, found: {text}")

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


async def read_rendered_titles(
        page: playwright.async_api._generated.Page,
        expected_count: int,
        expected_titles: list[str] | None = None,
        timeout_ms: int = 10000,
) -> list[str]:
    """
    Read archive titles from the rendered rows, in display order.

    Wraps `read_rendered_entries` and drops the arcid; the caller asserts on the titles.
    """
    entries = await read_rendered_entries(page, expected_count, expected_titles, timeout_ms)
    titles: list[str] = []
    for title, _ in entries:
        titles.append(title)
    return titles


async def wait_for_input_value(
        page: playwright.async_api._generated.Page,
        locator: playwright.async_api._generated.Locator,
        expected: str,
        timeout_ms: int = 10000,
) -> str:
    """
    Return the first `expected` observation or the last observation on timeout.
    """
    deadline = time.monotonic() + (timeout_ms / 1000)
    value = ""
    while True:
        try:
            value = await locator.input_value(timeout=1000)
        except Exception:  # noqa: BLE001 - element may be mid-rerender
            value = ""
        if value == expected or time.monotonic() >= deadline:
            return value
        await page.wait_for_timeout(200)


async def read_rendered_entries(
        page: playwright.async_api._generated.Page,
        expected_count: int,
        expected_titles: list[str] | None = None,
        timeout_ms: int = 10000,
) -> list[tuple[str, str]]:
    """
    Read (title, arcid) for each archive rendered in the current view, in display order.

    Both come from the archive link a user clicks: its text is the title, its href carries the
    id. Lets a test check rendered order and identity without reading the search response.

    Polls until the view holds `expected_count` links and, when `expected_titles` is given,
    until the rendered order matches it. When `expected_titles` is given it also supplies the
    expected count. On timeout the entries actually rendered are returned, so the caller's
    assertion reports the real mismatch.
    """
    if expected_titles is not None:
        expected_count = len(expected_titles)
    deadline = time.monotonic() + (timeout_ms / 1000)
    while True:
        grid = page.locator('#thumbs_container .id2 a[href*="/reader?id="]')
        links = grid if await grid.count() else page.locator('td.title a[href*="/reader?id="]')
        count = await links.count()
        expired = time.monotonic() >= deadline
        if count == expected_count or expired:
            entries: list[tuple[str, str]] = []
            titles: list[str] = []
            for i in range(count):
                link = links.nth(i)
                title = (await link.inner_text()).strip()
                href = await link.get_attribute("href") or ""
                match = re.search(r"[?&]id=([^&]+)", href)
                entries.append((title, match.group(1) if match else ""))
                titles.append(title)
            if expired or expected_titles is None or titles == expected_titles:
                return entries
        await page.wait_for_timeout(200)
