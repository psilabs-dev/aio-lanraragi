import logging
import time
from typing import Any, List
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
import pytest
from _pytest.nodes import Item
from _pytest.reports import TestReport
from _pytest.runner import CallInfo

logger = logging.getLogger(__name__)

# constants
DEFAULT_REDIS_TAG = "redis:7.2.4"
DEFAULT_LANRARAGI_TAG = "difegue/lanraragi"
DEFAULT_NETWORK_NAME = "default-network"

def pytest_addoption(parser: pytest.Parser):
    """
    Set up a self-contained environment for LANraragi integration testing.
    New containers/networks will be created on each session. If an exception or invalid
    event occurred, an attempt will be made to clean up all test objects.

    If running on a Windows machine, the `--windows-runfile` flag must be provided.

    Parameters
    ----------
    build : `str`
        Docker image build path to LANraragi project root directory. Overrides the `--image` flag.

    image : `str`
        Docker image tag to use for LANraragi image. Defaults to "difegue/lanraragi".

    docker-api : `bool = False`
        Use Docker API client. Requires privileged access to the Docker daemon, 
        but allows you to see build outputs.

    git-url : `str`
        URL of LANraragi git repository to build a Docker image from.

    git-branch : `str`
        Optional branch name of the corresponding git repository.

    windist : `str`
        Path to the original LRR app distribution bundle for Windows.

    staging : `str`
        Path to the LRR staging directory (where all host-based testing and file RW happens).

    experimental : `bool = False`
        Run experimental tests. For example, to test a set of LANraragi APIs in
        active development, but are yet merged upstream.

    failing : `bool = False`
        Run tests that are known to fail.

    npseed : `int = 42`
        Seed (in numpy) to set for any randomized behavior.
    """
    parser.addoption("--build", action="store", default=None, help="Path to docker build context for LANraragi.")
    parser.addoption("--image", action="store", default=None, help="LANraragi image to use.")
    parser.addoption("--git-url", action="store", default=None, help="Link to a LANraragi git repository (e.g. fork or branch).")
    parser.addoption("--git-branch", action="store", default=None, help="Branch to checkout; if not supplied, uses the main branch.")
    parser.addoption("--docker-api", action="store_true", default=False, help="Enable docker api to build image (e.g., to see logs). Needs access to unix://var/run/docker.sock.")
    parser.addoption("--windist", action="store", default=None, help="Path to the LRR app distribution for Windows.")
    parser.addoption("--staging", action="store", default=None, help="Path to the LRR staging directory (where all host-based testing and file RW happens).")
    parser.addoption("--lrr-debug", action="store_true", default=False, help="Enable debug mode for the LRR logs.")
    parser.addoption("--experimental", action="store_true", default=False, help="Run experimental tests.")
    parser.addoption("--failing", action="store_true", default=False, help="Run tests that are known to fail.")
    parser.addoption("--npseed", type=int, action="store", default=42, help="Seed (in numpy) to set for any randomized behavior.")

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

def pytest_sessionstart(session: pytest.Session):
    """
    Configure a global run ID for a pytest session.
    """
    config = session.config
    config.global_run_id = int(time.time() * 1000)
    global_run_id = config.global_run_id
    npseed: int = config.getoption("--npseed")
    logger.info(f"pytest run parameters: global_run_id={global_run_id}, npseed={npseed}")

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
        logger.info(f"Test failed: dumping logs... ({item.nodeid})")

        # # TODO: don't delete this tutorial; will probably use this in the future. (9/6/25)
        # # Log exception details from the report
        # if report.longrepr:
        #     logger.info(f"Exception details: {report.longrepr}")
        
        # # Access the raw exception from the call info if available
        # if hasattr(call, 'excinfo') and call.excinfo:
        #     excinfo = call.excinfo
        #     exc_class = excinfo.type
        #     exc_instance = excinfo.value
            
        #     logger.info(f"Exception class: {exc_class}")
        #     logger.info(f"Exception type name: {exc_class.__name__}")
        #     logger.info(f"Exception module: {exc_class.__module__}")
        #     logger.info(f"Exception value: {exc_instance}")
        #     logger.info(f"Exception message: {str(exc_instance)}")
            
        #     # Handle custom exceptions with error codes
        #     if hasattr(exc_instance, 'error_code'):
        #         logger.info(f"Custom error code: {exc_instance.error_code}")
            
        #     if hasattr(exc_instance, 'details'):
        #         logger.info(f"Custom details: {exc_instance.details}")
            
        #     # Pattern matching for specific exception types
        #     if exc_class == KeyError:
        #         logger.info("Handling KeyError: Missing key in dictionary/mapping")
        #     elif exc_class == ValueError:
        #         logger.info("Handling ValueError: Invalid value provided")
        #     elif exc_class.__name__ == 'AssertionError':
        #         logger.info("Handling AssertionError: Test assertion failed")
        #     elif exc_class.__module__ != 'builtins':
        #         logger.info(f"Custom exception detected from module: {exc_class.__module__}")
            
        #     # Check if it's a subclass of specific exceptions
        #     if issubclass(exc_class, ConnectionError):
        #         logger.info("This is a connection-related error")
        #     elif issubclass(exc_class, OSError):
        #         logger.info("This is an OS-related error")
        
        try:
            if hasattr(item.session, 'lrr_environment'):
                environment: AbstractLRRDeploymentContext = item.session.lrr_environment
                logger.error("\n\n >>>>> LRR LOGS >>>>>")
                environment.display_lrr_logs()
                logger.error("<<<<< LRR LOGS <<<<<\n\n")
            else:
                logger.warning("LRR environment not available in session for failure debugging")
        except Exception as e:
            logger.error(f"Failed to dump failure info: {e}")
