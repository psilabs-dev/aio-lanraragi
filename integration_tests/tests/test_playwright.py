import pytest

from aio_lanraragi_tests.utils.playwright import _browser_log_failure, _browser_log_url


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("net::ERR_CONNECTION_FAILED", "net::ERR_CONNECTION_FAILED"),
        ("ERR_TIMED_OUT", "ERR_TIMED_OUT"),
        ("NS_ERROR_NET_TIMEOUT", "NS_ERROR_NET_TIMEOUT"),
        ("net::ERR_FAILED for /private", "request failed"),
        ("net::ERR_FAILED\nmore details", "request failed"),
        ("", "request failed"),
        (None, "request failed"),
    ],
)
def test_browser_log_failure(failure: str | None, expected: str) -> None:
    assert _browser_log_failure(failure) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://user:password@127.0.0.1:3010/index?token=hidden#details", "http://127.0.0.1:3010/index"),
        ("http://127.0.0.1:3011/webhook/private", "http://127.0.0.1:3011"),
        ("https://example.com/webhook/private?token=hidden", "https://example.com:443"),
        ("data:text/html,private", "data:[redacted]"),
        ("http://example.com:bogus/private", "[invalid URL]"),
    ],
)
def test_browser_log_url(url: str, expected: str) -> None:
    assert _browser_log_url(url, "http://127.0.0.1:3010") == expected
