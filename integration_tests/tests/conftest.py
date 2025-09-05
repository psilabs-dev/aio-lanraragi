import logging
from typing import Any, List
import pytest
from _pytest.nodes import Item
from _pytest.reports import TestReport
from _pytest.runner import CallInfo

from aio_lanraragi_tests.lrr_docker import LRREnvironment

logger = logging.getLogger(__name__)

# constants
DEFAULT_REDIS_TAG = "redis:7.2.4"
DEFAULT_LANRARAGI_TAG = "difegue/lanraragi"
DEFAULT_NETWORK_NAME = "default-network"

def pytest_addoption(parser: pytest.Parser):
    """
    Set up a self-contained docker environment for LANraragi integration testing.
    New containers/networks will be created on each session. If an exception or invalid
    event occurred, an attempt will be made to clean up all test objects.

    Parameters
    ----------
    build_path : `str`
        Path to LANraragi project root directory. Overrides the `--image` flag.
    
    image : `str`
        Tag of LANraragi image to use. Defaults to "difegue/lanraragi".

    docker-api : `bool = False`
        Use Docker API client. Requires privileged access to the Docker daemon, 
        but allows you to see build outputs.

    git-url : `str`
        URL of LANraragi git repository to build an image from.

    git-branch : `str`
        Optional branch name of the corresponding git repository.
    
    experimental : `bool = False`
        Run experimental tests. For example, to test a set of LANraragi APIs in
        active development, but are yet merged upstream.
    """
    parser.addoption("--build", action="store", default=None, help="Path to docker build context for LANraragi.")
    parser.addoption("--image", action="store", default=DEFAULT_LANRARAGI_TAG, help="LANraragi image to use.")
    parser.addoption("--git-url", action="store", default=None, help="Link to a LANraragi git repository (e.g. fork or branch).")
    parser.addoption("--git-branch", action="store", default=None, help="Branch to checkout; if not supplied, uses the main branch.")
    parser.addoption("--docker-api", action="store_true", default=False, help="Enable docker api to build image (e.g., to see logs). Needs access to unix://var/run/docker.sock.")
    parser.addoption("--experimental", action="store_true", default=False, help="Run experimental tests.")
    parser.addoption("--failing", action="store_true", default=False, help="Run tests that are known to fail.")

def pytest_configure(config: pytest.Config):
    config.addinivalue_line(
        "markers",
        "experimental: Experimental tests will be skipped by default."
    )
    config.addinivalue_line(
        "markers",
        "failing: Tests that are known to fail will be skipped by default."
    )

def pytest_collection_modifyitems(config: pytest.Config, items: List[pytest.Item]):
    if not config.getoption("--experimental"):
        skip_experimental = pytest.mark.skip(reason="need --experimental option enabled")
        for item in items:
            if 'experimental' in item.keywords:
                item.add_marker(skip_experimental)
    if not config.getoption("--failing"):
        skip_failing = pytest.mark.skip(reason="need --failing option enabled")
        for item in items:
            if 'failing' in item.keywords:
                item.add_marker(skip_failing)

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: Item, call: CallInfo[Any]):
    """
    Some logic to allow pytest to retrieve the LRR environment during a test failure.
    Dumps LRR logs from environment before containers are cleaned up as error logs.

    To see these logs, include `--log-cli-level=ERROR`.
    """
    outcome = yield
    report: TestReport = outcome.get_result()
    if report.when == "call" and report.failed:
        logger.error(f"Test failed: {item.nodeid}")
        try:
            if hasattr(item.session, 'lrr_environment'):
                environment: LRREnvironment = item.session.lrr_environment
                lrr_logs = environment.get_lrr_logs()
                if lrr_logs:
                    log_text = lrr_logs.decode('utf-8', errors='replace')
                    for line in log_text.split('\n'):
                        if line.strip():
                            environment.logger.error(f"LRR: {line}")
            else:
                logger.warning("LRR environment not available in session for failure debugging")
        except Exception as e:
            logger.error(f"Failed to dump failure info: {e}")
