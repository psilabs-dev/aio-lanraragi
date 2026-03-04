import logging
import platform
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext

LOGGER = logging.getLogger(__name__)

# constants
DEFAULT_REDIS_TAG = "redis:7.2.4"
DEFAULT_LANRARAGI_TAG = "difegue/lanraragi"
DEFAULT_NETWORK_NAME = "default-network"

def pytest_addoption(parser: pytest.Parser):
    """
    Set up a self-contained environment for LANraragi integration testing.
    New containers/networks will be created on each session. If an exception or invalid
    event occurred, an attempt will be made to clean up all test objects.

    If running on a Windows machine, the `--windist` path must be provided.

    Parameters
    ----------
    build : `str`
        Docker image build path to LANraragi project root directory. Overrides the `--image` flag.

    image : `str`
        Docker image tag to use for LANraragi image. Defaults to "difegue/lanraragi".

    docker-api : `bool = False`
        Use Docker API client. Requires privileged access to the Docker daemon,
        but allows you to see build outputs.

    dockerfile : `str`
        Path to a custom Dockerfile.
        Cannot be combined with --git-url or --image.

    git-url : `str`
        URL of LANraragi git repository to build a Docker image from.

    git-branch : `str`
        Optional branch name of the corresponding git repository.

    windist : `str`
        Path to the original LRR app distribution bundle for Windows.

    staging : `str`
        Path to the LRR staging directory (where all host-based testing and file RW happens).

    dev : `list[str]`
        Run dev tests for one or more scopes. Repeat this flag to enable
        multiple scopes (e.g. ``--dev <scope>``).

    playwright : `bool = False`
        Run UI integration tests requiring Playwright.

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
    parser.addoption("--dockerfile", action="store", default=None, help="Path to a custom Dockerfile. If relative, resolved relative to --build. Cannot be combined with --git-url or --image.")
    parser.addoption("--windist", action="store", default=None, help="Path to the LRR app distribution for Windows.")
    parser.addoption("--staging", action="store", default=Path.cwd() / ".staging", help="Path to the LRR staging directory (defaults to .staging).")
    parser.addoption("--lrr-debug", action="store_true", default=False, help="Enable debug mode for the LRR logs.")
    parser.addoption(
        "--dev",
        action="append",
        default=[],
        metavar="SCOPE",
        help="Run dev tests for one or more scopes. Repeat option to add more scopes.",
    )
    parser.addoption("--playwright", action="store_true", default=False, help="Run Playwright UI tests. Requires `playwright install`")
    parser.addoption("--failing", action="store_true", default=False, help="Run tests that are known to fail.")
    parser.addoption("--npseed", type=int, action="store", default=42, help="Seed (in numpy) to set for any randomized behavior.")

def pytest_configure(config: pytest.Config):
    config.addinivalue_line(
        "markers",
        "dev(scope): Dev tests with a required scope label."
    )
    config.addinivalue_line(
        "markers",
        "playwright: Playwright UI tests will be skipped by default."
    )
    config.addinivalue_line(
        "markers",
        "failing: Tests that are known to fail will be skipped by default."
    )

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]):
    if not config.getoption("--playwright"):
        skip_playwright = pytest.mark.skip(reason="need --playwright option enabled")
        for item in items:
            if 'playwright' in item.keywords:
                item.add_marker(skip_playwright)

    selected_dev_scopes = set(config.getoption("--dev") or [])
    for item in items:
        if 'dev' not in item.keywords:
            continue

        item_scopes: set[str] = set()
        for marker in item.iter_markers(name="dev"):
            if not marker.args:
                raise pytest.UsageError(
                    f"{item.nodeid} uses @pytest.mark.dev without a scope. "
                    "Use @pytest.mark.dev('scope')."
                )
            for scope in marker.args:
                if not isinstance(scope, str) or not scope.strip():
                    raise pytest.UsageError(
                        f"{item.nodeid} has invalid dev scope {scope!r}. "
                        "Scopes must be non-empty strings."
                    )
                item_scopes.add(scope.strip())

        if not selected_dev_scopes:
            item.add_marker(pytest.mark.skip(reason="need --dev <scope> option enabled"))
            continue

        if not item_scopes.intersection(selected_dev_scopes):
            requested = ", ".join(sorted(selected_dev_scopes))
            available = ", ".join(sorted(item_scopes))
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "dev scope not selected "
                        f"(requested: {requested}; test scopes: {available})"
                    )
                )
            )
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
    LOGGER.info(
        f"pytest run parameters: global_run_id={global_run_id}, npseed={npseed}"
    )

    cpu_count = psutil.cpu_count(logical=True)
    mem = psutil.virtual_memory()
    system = platform.system()
    version = platform.version()
    machine = platform.machine()
    LOGGER.info(
        f"system_profile: system={system} version={version} machine={machine} "
        f"cpu_count={cpu_count} total_mem_gb={mem.total / (1024 ** 3):.2f} "
        f"avail_mem_gb={mem.available / (1024 ** 3):.2f}"
    )

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    """
    Some logic to allow pytest to retrieve the LRR environment during a test failure.
    Dumps LRR logs from environment before containers are cleaned up as error logs.

    To see these logs, include `--log-cli-level=ERROR`.
    """
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    if report.when in ("call", "setup") and report.failed:
        if excinfo := call.excinfo:
            LOGGER.error(f"Test threw {excinfo.typename} with message \"{excinfo.value}\": dumping logs... ({item.nodeid}, phase={report.when})")
        else:
            LOGGER.error(f"Test failed: dumping logs... ({item.nodeid}, phase={report.when})")
        try:
            if hasattr(item.session, 'lrr_environments') and item.session.lrr_environments:
                environments_by_prefix: dict[str, AbstractLRRDeploymentContext] = item.session.lrr_environments
                for prefix, environment in environments_by_prefix.items():
                    LOGGER.error(f">>>>> LRR LOGS (prefix: \"{prefix}\") >>>>>")
                    lrr_logs = environment.read_lrr_logs()
                    lines = lrr_logs.split('\n')[-100:]
                    for line in lines:
                        LOGGER.error(line)
                    LOGGER.error(f"<<<<< LRR LOGS (prefix: \"{prefix}\") <<<<<")
                    LOGGER.error(f">>>>> SHINOBU LOGS (prefix: \"{prefix}\") >>>>>")
                    shinobu_logs = environment.read_log(environment.shinobu_logs_path)
                    lines = shinobu_logs.split('\n')[-100:]
                    for line in lines:
                        LOGGER.error(line)
                    LOGGER.error(f"<<<<< SHINOBU LOGS (prefix: \"{prefix}\") <<<<<")
                    LOGGER.error(f">>>>> REDIS LOGS (prefix: \"{prefix}\") >>>>>")
                    environment.display_redis_logs()
                    LOGGER.error(f"<<<<< REDIS LOGS (prefix: \"{prefix}\") <<<<<")
            else:
                LOGGER.info("No environment available.")
        except Exception as e:  # noqa: BLE001 — best-effort log dump
            LOGGER.error(f"Failed to dump failure info: {e}")
