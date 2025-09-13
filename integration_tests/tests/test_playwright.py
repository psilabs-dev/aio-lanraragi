import logging
import sys
import docker
from playwright.async_api import Page, expect
import pytest
import pytest_asyncio

from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.windows import WindowsLRRDeploymentContext
from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.common import DEFAULT_API_KEY

logger = logging.getLogger(__name__)

@pytest.fixture
def environment(request: pytest.FixtureRequest):
    """
    Provides an uninitialized LRR environment for testing.
    """

    global_run_id: int = request.config.global_run_id

    # TODO: this should be refactored.
    environment: AbstractLRRDeploymentContext = None

    # check operating system.
    match sys.platform:
        case 'win32':
            runfile_path: str = request.config.getoption("--windows-runfile")
            windows_content_path: str = request.config.getoption("--windows-content-path")
            environment = WindowsLRRDeploymentContext(
                runfile_path, windows_content_path,
            )

        case 'darwin' | 'linux':
            # TODO: we're assuming macos is used as a development environment with docker installed,
            # not a testing environment; for macos github runners, we would be using them
            # to run homebrew integration tests.

            build_path: str = request.config.getoption("--build")
            image: str = request.config.getoption("--image")
            git_url: str = request.config.getoption("--git-url")
            git_branch: str = request.config.getoption("--git-branch")
            use_docker_api: bool = request.config.getoption("--docker-api")
            docker_client = docker.from_env()
            docker_api = docker.APIClient(base_url="unix://var/run/docker.sock") if use_docker_api else None
            environment = DockerLRRDeploymentContext(
                build_path, image, git_url, git_branch, docker_client, docker_api=docker_api,
                global_run_id=global_run_id, is_allow_uploads=True
            )

    # environment.setup(with_api_key=True, with_nofunmode=True)
    request.session.lrr_environment = environment # Store environment in pytest session for access in hooks
    yield environment
    environment.teardown(remove_data=True)

@pytest_asyncio.fixture
async def lanraragi():
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    client = LRRClient(
        lrr_host="http://localhost:3001",
        lrr_api_key=DEFAULT_API_KEY,
        timeout=10
    )
    try:
        yield client
    finally:
        await client.close()

@pytest.mark.asyncio
@pytest.mark.experimental
async def test_failed_login(environment: AbstractLRRDeploymentContext, page: Page):
    environment.setup(with_api_key=False, with_nofunmode=True)
    logger.info("Visiting test LRR page...")
    await page.goto("http://127.0.0.1:3001")
    await expect(page.get_by_text("I'm under Japanese influence and my honor's at stake!", exact=True)).to_be_visible()
    logger.info("Clicking login button.")
    await page.get_by_role("button", name="Login").click()
    await expect(page.get_by_text("Wrong Password.", exact=True)).to_be_visible()
