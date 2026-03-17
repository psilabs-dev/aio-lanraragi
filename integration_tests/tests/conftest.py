import logging
import platform
import re
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.docker_postgres import (
    DockerPostgresLRRDeploymentContext,
)

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
        Absolute path to LANraragi project root directory for Docker image build.
        Overrides the `--image` flag.

    image : `str`
        Docker image tag to use for LANraragi image. Defaults to "difegue/lanraragi".

    docker-api : `bool = False`
        Use Docker API client. Requires privileged access to the Docker daemon,
        but allows you to see build outputs.

    dockerfile : `str`
        Path to a custom Dockerfile.
        Cannot be combined with --git-url or --image.

    build-ref : `str`
        Git ref (commit hash, branch, tag) to check out before building from --build path.
        Requires --build.

    git-url : `str`
        URL of LANraragi git repository to build a Docker image from.

    git-ref : `str`
        Optional git ref (branch, tag, commit) of the corresponding git repository.

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

    regression : `bool = False`
        Run regression tests (behavioral expectation and e2e tests).

    benchmark : `bool = False`
        Run benchmark tests.

    no-rate-limit : `bool = False`
        Skip tests that depend on rate-limited external resources.

    npseed : `int = 42`
        Seed (in numpy) to set for any randomized behavior.

    port-offset : `int = 0`
        Session-wide base port offset added to per-module offsets.

    resource-prefix : `str = ""`
        Session-wide prefix prepended to per-module resource prefixes.
    """
    parser.addoption("--build", action="store", default=None, help="Absolute path to docker build context for LANraragi.")
    parser.addoption("--build-ref", action="store", default=None, help="Git ref (commit, branch, tag) to checkout before building. Requires --build.")
    parser.addoption("--image", action="store", default=None, help="LANraragi image to use.")
    parser.addoption("--git-url", action="store", default=None, help="Link to a LANraragi git repository (e.g. fork or branch).")
    parser.addoption("--git-ref", action="store", default=None, help="Git ref to checkout; if not supplied, uses the default branch.")
    parser.addoption("--docker-api", action="store_true", default=False, help="Enable docker api to build image (e.g., to see logs). Needs access to unix://var/run/docker.sock.")
    parser.addoption("--dockerfile", action="store", default=None, help="Path to a custom Dockerfile. If relative, resolved relative to --build. Cannot be combined with --git-url or --image.")
    parser.addoption("--windist", action="store", default=None, help="Path to the LRR app distribution for Windows.")
    parser.addoption("--staging", action="store", default=Path.cwd() / ".staging", help="Path to the LRR staging directory (defaults to .staging).")
    parser.addoption("--server-logs", action="store", default=None, help="Absolute path to generated test-time server logs directory (not saved by default).")
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
    parser.addoption("--regression", action="store_true", default=False, help="Run regression tests (behavioral expectation and e2e tests).")
    parser.addoption("--benchmark", action="store_true", default=False, help="Run benchmark tests.")
    parser.addoption("--benchmark-output", action="store", default=None, help="Path to write benchmark results JSON.")
    parser.addoption("--benchmark-label", action="store", default=None, help="Label for this benchmark run (e.g. c0-r0).")
    parser.addoption("--no-rate-limit", action="store_true", default=False, help="Skip tests that depend on rate-limited external resources (e.g. raw.githubusercontent.com).")
    parser.addoption("--npseed", type=int, action="store", default=42, help="Seed (in numpy) to set for any randomized behavior.")
    parser.addoption("--cache-backend", action="store", default="redis", choices=["redis", "valkey", "valkey8"], help="Cache backend for Docker deployments. Default: redis.")
    parser.addoption("--port-offset", type=int, action="store", default=0, help="Session-wide base port offset added to per-module offsets. Use to avoid conflicts between parallel sessions.")
    parser.addoption("--resource-prefix", action="store", default="", help="Session-wide prefix prepended to per-module resource prefixes. Use to isolate parallel sessions.")
    parser.addoption("--postgres", action="store_true", default=False, help="Deploy PostgreSQL alongside Redis for metadata/search (Docker only).")
    parser.addoption(
        "--no-postgres-jit", action="store_false", default=True, dest="postgres_jit",
        help="Disable PostgreSQL JIT compilation (enabled by default).",
    )

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
    config.addinivalue_line(
        "markers",
        "regression: Regression tests will be skipped by default."
    )
    config.addinivalue_line(
        "markers",
        "benchmark: Benchmarking tests will be skipped by default."
    )
    config.addinivalue_line(
        "markers",
        "ratelimit: Tests that depend on rate-limited external resources."
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

    if not config.getoption("--regression"):
        skip_regression = pytest.mark.skip(reason="need --regression option enabled")
        for item in items:
            if 'regression' in item.keywords:
                item.add_marker(skip_regression)

    if not config.getoption("--benchmark"):
        skip_benchmark = pytest.mark.skip(reason="need --benchmark option enabled")
        for item in items:
            if 'benchmark' in item.keywords:
                item.add_marker(skip_benchmark)

    if config.getoption("--no-rate-limit"):
        skip_ratelimit = pytest.mark.skip(reason="--no-rate-limit excludes rate-limited tests")
        for item in items:
            if 'ratelimit' in item.keywords:
                item.add_marker(skip_ratelimit)

def pytest_sessionstart(session: pytest.Session):
    """
    Configure a global run ID for a pytest session.
    """
    config = session.config

    server_logs = config.getoption("--server-logs")
    if server_logs is not None and not Path(server_logs).is_absolute():
        raise pytest.UsageError("--server-logs must be an absolute path.")

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

def _sanitize_nodeid(nodeid: str) -> str:
    """Sanitize a pytest node ID into a filesystem-safe directory name."""
    return re.sub(r'[^\w.\-]', '_', nodeid)


def _save_server_logs(
    server_logs_dir: Path,
    nodeid: str,
    environments: dict[str, AbstractLRRDeploymentContext],
):
    """
    Save full server logs for each environment to the server-logs directory.

    For LRR and Redis, file-based log readers are tried first. If they return
    empty (e.g. LRR crashed before writing logs), process-level logs
    (container stdout for Docker, console output deque for Windows) are used
    as a fallback.
    """
    sanitized = _sanitize_nodeid(nodeid)
    for prefix, environment in environments.items():
        out_dir = server_logs_dir / sanitized / prefix
        out_dir.mkdir(parents=True, exist_ok=True)

        # LRR logs: file-based, fallback to process logs.
        lrr_logs = environment.read_lrr_logs()
        if not lrr_logs:
            lrr_logs = environment.get_lrr_logs(tail=10000).decode('utf-8', errors='replace')
        if lrr_logs:
            (out_dir / "lanraragi.log").write_text(lrr_logs, encoding='utf-8')

        if environment.shinobu_logs_path.exists():
            shinobu_logs = environment.read_log(environment.shinobu_logs_path)
            if shinobu_logs:
                (out_dir / "shinobu.log").write_text(shinobu_logs, encoding='utf-8')

        mojo_logs = environment.read_mojo_logs()
        if mojo_logs:
            (out_dir / "mojo.log").write_text(mojo_logs, encoding='utf-8')

        # Redis logs: file-based, fallback to process logs.
        redis_logs = environment.read_redis_logs()
        if not redis_logs:
            redis_logs = environment.get_redis_logs(tail=10000).decode('utf-8', errors='replace')
        if redis_logs:
            (out_dir / "redis.log").write_text(redis_logs, encoding='utf-8')

        # Postgres logs (only available for postgres deployments).
        if isinstance(environment, DockerPostgresLRRDeploymentContext):
            postgres_logs = environment.get_postgres_logs(tail=10000).decode('utf-8', errors='replace')
            if postgres_logs:
                (out_dir / "postgres.log").write_text(postgres_logs, encoding='utf-8')


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    """
    Save server logs to the --server-logs directory on test failure.
    """
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    if report.when in ("call", "setup") and report.failed:
        server_logs_dir = item.config.getoption("--server-logs")
        if not server_logs_dir:
            return
        server_logs_dir = Path(server_logs_dir)
        try:
            if hasattr(item.session, 'lrr_environments') and item.session.lrr_environments:
                environments_by_prefix: dict[str, AbstractLRRDeploymentContext] = item.session.lrr_environments
                _save_server_logs(server_logs_dir, item.nodeid, environments_by_prefix)
                LOGGER.info(f"Server logs saved to {server_logs_dir / _sanitize_nodeid(item.nodeid)}")
            else:
                LOGGER.info("No environment available for log collection.")
        except Exception as e:  # noqa: BLE001 — best-effort log save
            LOGGER.error(f"Failed to save server logs: {e}")
