from unittest.mock import Mock

from lanraragi.clients.client import LRRClient
from playwright.async_api import BrowserContext, Page, Request, Response

from aio_lanraragi_tests.utils.playwright import PlaywrightTestContextManager


def test_browser_log_snapshot_preserves_playwright_details() -> None:
    page = Mock(spec=Page, url="about:blank#reader")
    browser_context = Mock(spec=BrowserContext, pages=[page])
    pending_request = Mock(
        spec=Request,
        method="GET",
        resource_type="fetch",
        url="http://127.0.0.1:3010/api/search?search=language%3Aenglish",
    )
    failed_request = Mock(
        spec=Request,
        method="GET",
        url="https://example.test/releases/latest",
        failure="Connection closed while receiving headers",
    )
    error_url = "https://example.test/releases/download?version=dev"
    error_request = Mock(spec=Request, method="GET", url=error_url)
    error_response = Mock(
        spec=Response,
        request=error_request,
        status=503,
        url=error_url,
    )
    context = PlaywrightTestContextManager(Mock(spec=LRRClient))
    context._browser_context = browser_context
    context._record_request_start(pending_request)
    context.failed_requests.append(failed_request)
    context.responses.append(error_response)

    snapshot = context._browser_log_snapshot()

    assert "Page: about:blank#reader" in snapshot
    assert f"GET {pending_request.url} (fetch, no response headers," in snapshot
    assert f"GET {failed_request.url} ({failed_request.failure})" in snapshot
    assert f"GET {error_response.url} (HTTP 503)" in snapshot
