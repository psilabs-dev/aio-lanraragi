import asyncio
import logging
import numpy as np
import sys
from typing import Generator, List
import docker
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
                windist, "test_", 10
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
                build_path, image, git_url, git_branch, docker_client, "test_", 10, docker_api=docker_api,
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

async def sample_test_api_auth_matrix(
    is_nofunmode: bool, is_api_key_configured_server: bool, is_api_key_configured_client: bool,
    is_matching_api_key: bool, deployment_context: AbstractLRRDeploymentContext, lrr_client: LRRClient
):
    # sanity check.
    if is_matching_api_key and ((not is_api_key_configured_client) or (not is_api_key_configured_server)):
        raise ValueError("is_matching_api_key must have configured API keys for client and server.")

    # configuration stage.
    if is_nofunmode:
        deployment_context.enable_nofun_mode()
    else:
        deployment_context.disable_nofun_mode()
    if is_api_key_configured_server:
        deployment_context.update_api_key(DEFAULT_API_KEY)
    else:
        deployment_context.update_api_key(None)
    if is_api_key_configured_client:
        if is_matching_api_key:
            lrr_client.update_api_key(DEFAULT_API_KEY)
        else:
            lrr_client.update_api_key(DEFAULT_API_KEY+"wrong")
    else:
        lrr_client.update_api_key(None)

    def endpoint_permission_granted(endpoint_is_public: bool) -> bool:
        """
        Returns True if the permission is granted for an API call given a set of configurations, 
        and False otherwise.

        There are probably a dozen other ways to express this function.
        """
        require_valid_api_key = is_api_key_configured_server and is_api_key_configured_client and is_matching_api_key

        if endpoint_is_public:
            if is_nofunmode:
                return require_valid_api_key
            else:
                return True
        else: # nofunmode doesn't matter.
            return require_valid_api_key

    # apply configurations
    deployment_context.restart()

    # test public endpoint.
    endpoint_is_public = True
    for method in [
        lrr_client.archive_api.get_all_archives,
        lrr_client.category_api.get_all_categories,
        lrr_client.misc_api.get_server_info
    ]:
        response, error = await method()
        method_name = method.__name__
        
        if endpoint_permission_granted(endpoint_is_public):
            assert not error, f"API call failed for method {method_name} (status {error.status}): {error.error}"
        else:
            assert not response, f"Expected forbidden error from calling {method_name}, got response: {response}"
            assert error.status == 401, f"Expected status 401, got: {error.status}."

    # test protected endpoint.
    endpoint_is_public = False
    for method in [
        lrr_client.shinobu_api.get_shinobu_status,
        lrr_client.database_api.get_database_backup
    ]:
        response, error = await method()
        method_name = method.__name__

        if endpoint_permission_granted(endpoint_is_public):
            assert not error, f"API call failed for method {method_name} (status {error.status}): {error.error}"
        else:
            assert not response, f"Expected forbidden error from calling {method_name}, got response: {response}"
            assert error.status == 401, f"Expected status 401, got: {error.status}."

@pytest.mark.asyncio
async def test_api_auth_matrix(
    environment: AbstractLRRDeploymentContext, lanraragi: LRRClient, npgenerator: np.random.Generator, port_offset: int, is_lrr_debug_mode: bool
):
    """
    Test the following situation combinations:
    - whether nofunmode is configured
    - whether endpoint is public or protected
    - whether API key is set by server
    - whether API key is passed by client
    - whether client API key equals server API key

    sample public endpoints to use:
    - GET /api/archives
    - GET /api/categories
    - GET /api/tankoubons
    - GET /api/info

    sample protected endpoints to use:
    - GET /api/shinobu
    - GET /api/database/backup
    """
    # initialize the server.
    environment.setup(with_api_key=False, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)

    # generate the parameters list, then randomize it to remove ordering effect.
    test_params: List[ApiAuthMatrixParams] = []
    for is_nofunmode in [True, False]:
        for is_api_key_configured_server in [True, False]:
            for is_api_key_configured_client in [True, False]:
                if is_api_key_configured_client and is_api_key_configured_server:
                    for is_matching_api_key in [True, False]:
                        test_params.append(ApiAuthMatrixParams(
                            is_nofunmode=is_nofunmode, is_api_key_configured_server=is_api_key_configured_server,
                            is_api_key_configured_client=is_api_key_configured_client,
                            is_matching_api_key=is_matching_api_key
                        ))
                else:
                    is_matching_api_key = False
                    test_params.append(ApiAuthMatrixParams(
                        is_nofunmode=is_nofunmode, is_api_key_configured_server=is_api_key_configured_server,
                        is_api_key_configured_client=is_api_key_configured_client,
                        is_matching_api_key=is_matching_api_key
                    ))

    npgenerator.shuffle(test_params)
    num_tests = len(test_params)

    # execute tests with randomized order of configurations.
    for i, test_param in enumerate(test_params):
        logger.info(f"Test configuration ({i+1}/{num_tests}): is_nofunmode={test_param.is_nofunmode}, is_apikey_configured_server={test_param.is_api_key_configured_server}, is_apikey_configured_client={test_param.is_api_key_configured_client}, is_matching_api_key={test_param.is_matching_api_key}")
        await sample_test_api_auth_matrix(
            test_param.is_nofunmode, test_param.is_api_key_configured_server, test_param.is_api_key_configured_client,
            test_param.is_matching_api_key, environment, lanraragi
        )
