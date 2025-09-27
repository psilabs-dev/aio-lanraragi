import asyncio
import logging
from aio_lanraragi_tests.deployment.factory import generate_deployment
import aiohttp
import numpy as np
from typing import Generator, List
from pydantic import BaseModel, Field
import pytest
import pytest_asyncio

from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import DEFAULT_API_KEY

LOGGER = logging.getLogger(__name__)

class ApiAuthMatrixParams(BaseModel):
    # used by test_api_auth_matrix.
    is_nofunmode: bool
    is_api_key_configured_server: bool
    is_api_key_configured_client: bool
    is_matching_api_key: bool = Field(..., description="Set to False if not is_api_key_configured_server or not is_api_key_configured_client")

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture
def is_lrr_debug_mode(request: pytest.FixtureRequest) -> Generator[bool, None, None]:
    yield request.config.getoption("--lrr-debug")

@pytest.fixture
def environment(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int) -> Generator[AbstractLRRDeploymentContext, None, None]:
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    request.session.lrr_environment = environment
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
async def lanraragi(environment: AbstractLRRDeploymentContext) -> Generator[LRRClient, None, None]:
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    connector = aiohttp.TCPConnector(limit=8, limit_per_host=8, keepalive_timeout=30)
    client = environment.lrr_client(connector=connector)
    try:
        yield client
    finally:
        await client.close()
        await connector.close()

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
        LOGGER.info(f"Test configuration ({i+1}/{num_tests}): is_nofunmode={test_param.is_nofunmode}, is_apikey_configured_server={test_param.is_api_key_configured_server}, is_apikey_configured_client={test_param.is_api_key_configured_client}, is_matching_api_key={test_param.is_matching_api_key}")
        await sample_test_api_auth_matrix(
            test_param.is_nofunmode, test_param.is_api_key_configured_server, test_param.is_api_key_configured_client,
            test_param.is_matching_api_key, environment, lanraragi
        )
