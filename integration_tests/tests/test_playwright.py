import asyncio
import logging
import numpy as np
import sys
from typing import Generator
import docker
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field
import pytest
import pytest_asyncio

from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.windows import WindowsLRRDeploymentContext
from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.common import DEFAULT_API_KEY, DEFAULT_LRR_PORT

logger = logging.getLogger(__name__)

class ApiAuthMatrixParams(BaseModel):
    # used by test_api_auth_matrix.
    is_nofunmode: bool
    is_api_key_configured_server: bool
    is_api_key_configured_client: bool
    is_matching_api_key: bool = Field(..., description="Set to False if not is_api_key_configured_server or not is_api_key_configured_client")

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture
def is_lrr_debug_mode(request: pytest.FixtureRequest) -> Generator[bool, None, None]:
    yield request.config.getoption("--lrr-debug")

@pytest.fixture
def environment(request: pytest.FixtureRequest) -> Generator[AbstractLRRDeploymentContext, None, None]:
    """
    Provides an uninitialized LRR environment for testing.
    """

    global_run_id: int = request.config.global_run_id

    # TODO: this should be refactored.
    environment: AbstractLRRDeploymentContext = None

    # check operating system.
    match sys.platform:
        case 'win32':
            windist: str = request.config.getoption("--windist")
            environment = WindowsLRRDeploymentContext(
                windist,
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

@pytest.fixture
def npgenerator(request: pytest.FixtureRequest) -> Generator[np.random.Generator, None, None]:
    seed: int = int(request.config.getoption("npseed"))
    generator = np.random.default_rng(seed)
    yield generator

@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield asyncio.BoundedSemaphore(value=8)

@pytest_asyncio.fixture
async def lanraragi(port_offset: int) -> Generator[LRRClient, None, None]:
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    client = LRRClient(
        lrr_host=f"http://localhost:{DEFAULT_LRR_PORT + port_offset}",
        lrr_api_key=DEFAULT_API_KEY,
        timeout=10
    )
    try:
        yield client
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_login(environment: AbstractLRRDeploymentContext, port_offset: int, is_lrr_debug_mode: bool):
    """
    Test login functionality.
    """

    environment.setup("test_", port_offset, with_api_key=False, with_nofunmode=True, lrr_debug_mode=is_lrr_debug_mode)
    port = environment.get_lrr_port()
    url = f"http://127.0.0.1:{port}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        logger.info(f"Visiting port {url}")
        await page.goto(url)
        await page.wait_for_load_state("networkidle")
