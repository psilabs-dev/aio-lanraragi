import asyncio
import logging
import aiohttp
import numpy as np
from typing import Dict, Generator, List
import playwright.async_api
from pydantic import BaseModel, Field
import pytest
import pytest_asyncio

from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.common import DEFAULT_API_KEY, DEFAULT_LRR_PASSWORD, LRR_INDEX_TITLE, LRR_LOGIN_TITLE
from aio_lanraragi_tests.helpers import expect_no_error_logs
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext

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

    # configure environments to session
    environments: Dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

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
async def lrr_client(environment: AbstractLRRDeploymentContext) -> Generator[LRRClient, None, None]:
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
    is_matching_api_key: bool, environment: AbstractLRRDeploymentContext, lrr_client: LRRClient
):
    # sanity check.
    if is_matching_api_key and ((not is_api_key_configured_client) or (not is_api_key_configured_server)):
        raise ValueError("is_matching_api_key must have configured API keys for client and server.")

    # configuration stage.
    if is_nofunmode:
        environment.enable_nofun_mode()
    else:
        environment.disable_nofun_mode()
    if is_api_key_configured_server:
        environment.update_api_key(DEFAULT_API_KEY)
    else:
        environment.update_api_key(None)
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
    environment.restart()

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

    # test main page.
    # playwright uses English as locale and timezone, if this changes in the future we may need to update.
    expected_title = LRR_LOGIN_TITLE if is_nofunmode else LRR_INDEX_TITLE
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        assert await page.title() == expected_title
        await browser.close()

    # check logs for errors
    expect_no_error_logs(environment)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_ui_nofunmode_login_right_password(environment: AbstractLRRDeploymentContext, is_lrr_debug_mode: bool, lrr_client: LRRClient):
    """
    Login with correct password.
    """
    environment.setup(with_nofunmode=True, lrr_debug_mode=is_lrr_debug_mode)

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        assert await page.title() == LRR_LOGIN_TITLE

        # right password test
        await page.fill("#pw_field", DEFAULT_LRR_PASSWORD)
        await page.click("input[type='submit'][value='Login']")
        await page.wait_for_load_state("networkidle")
        assert await page.title() == LRR_INDEX_TITLE

    # check logs for errors
    expect_no_error_logs(environment)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_ui_nofunmode_login_empty_password(environment: AbstractLRRDeploymentContext, is_lrr_debug_mode: bool, lrr_client: LRRClient):
    """
    Login without password.
    """
    environment.setup(with_nofunmode=True, lrr_debug_mode=is_lrr_debug_mode)

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        assert await page.title() == LRR_LOGIN_TITLE

        # empty password test
        await page.click("input[type='submit'][value='Login']")
        await page.wait_for_load_state("networkidle")
        assert "Wrong Password." in await page.content()
        assert await page.title() == LRR_LOGIN_TITLE

    # check logs for errors
    expect_no_error_logs(environment)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_ui_nofunmode_login_wrong_password(environment: AbstractLRRDeploymentContext, is_lrr_debug_mode: bool, lrr_client: LRRClient):
    """
    Login with wrong password.
    """
    environment.setup(with_nofunmode=True, lrr_debug_mode=is_lrr_debug_mode)

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        assert await page.title() == LRR_LOGIN_TITLE

        # right password test
        await page.fill("#pw_field", "password")
        await page.click("input[type='submit'][value='Login']")
        await page.wait_for_load_state("networkidle")
        assert "Wrong Password." in await page.content()
        assert await page.title() == LRR_LOGIN_TITLE

    # check logs for errors
    expect_no_error_logs(environment)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_ui_enable_nofunmode(environment: AbstractLRRDeploymentContext, is_lrr_debug_mode: bool, lrr_client: LRRClient):
    """
    Simulate UI: enable nofunmode and check that login is enforced.
    """
    environment.setup(with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        assert await page.title() == LRR_INDEX_TITLE

        # enter admin portal
        # exit overlay
        if "New Version Release Notes" in await page.content():
            LOGGER.info("Closing new releases overlay.")
            await page.keyboard.press("Escape")

        assert "Admin Login" in await page.content(), "Admin Login not found!"

        LOGGER.info("Click Admin Login button")
        await page.get_by_role("link", name="Admin Login").click()
        assert await page.title() == LRR_LOGIN_TITLE

        LOGGER.info("Entering default password")
        await page.locator("#pw_field").fill(DEFAULT_LRR_PASSWORD)
        await page.get_by_role("button", name="Login").click()
        await page.wait_for_load_state("networkidle")
        assert await page.title() == LRR_INDEX_TITLE

        LOGGER.info("Clicking settings button.")
        await page.get_by_role("link", name="Settings").click()
        LOGGER.info("Clicking security settings.")
        await page.get_by_text("Security").click()
        LOGGER.info("Enabling No-Fun Mode.")
        await page.get_by_role("checkbox", name="Enabling No-Fun Mode will").check()
        LOGGER.info("Clicking save settings.")
        await page.get_by_role("button", name="Save Settings").click()

    environment.restart()

    LOGGER.info("Checking that LRR server is locked after restart.")
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(lrr_client.lrr_base_url)
        await page.wait_for_load_state("networkidle")
        assert await page.title() == LRR_LOGIN_TITLE

    # check logs for errors
    expect_no_error_logs(environment)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_api_auth_matrix(
    environment: AbstractLRRDeploymentContext, lrr_client: LRRClient, npgenerator: np.random.Generator, is_lrr_debug_mode: bool
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
            test_param.is_matching_api_key, environment, lrr_client
        )

@pytest.mark.asyncio
async def test_disable_cors_preflight(environment: AbstractLRRDeploymentContext, lrr_client: LRRClient, is_lrr_debug_mode: bool):
    """
    Test preflight header response from server when CORS is not configured.
    This is the default behavior for LRR.

    Tests will be done with a privileged endpoint.
    """
    environment.setup(enable_cors=False, lrr_debug_mode=is_lrr_debug_mode)
    api = lrr_client.build_url("/api/shinobu")
    async with (
        aiohttp.ClientSession(headers={"Origin": "https://www.example.com"}) as session,
        session.options(api) as response
    ):
        headers = response.headers

        # confirm the CORS headers aren't here.
        # this is the current default behavior (when disabled),
        # so we'll test with the strictest conditions.
        assert "Access-Control-Allow-Headers" not in headers, "Allowed headers not in headers when CORS is enabled."
        assert "Access-Control-Allow-Methods" not in headers, "Allowed methods not present in headers when CORS is enabled."
        assert "Access-Control-Allow-Origin" not in headers, "Allowed origin not present in headers when CORS is enabled."

    # check logs for errors
    expect_no_error_logs(environment)

@pytest.mark.asyncio
async def test_enable_cors_preflight(environment: AbstractLRRDeploymentContext, lrr_client: LRRClient, is_lrr_debug_mode: bool):
    """
    Test preflight header response from server when CORS is enabled.
    """
    environment.setup(enable_cors=True, lrr_debug_mode=is_lrr_debug_mode)
    api = lrr_client.build_url("/api/shinobu")
    async with (
        aiohttp.ClientSession(headers={"Origin": "https://www.example.com"}) as session,
        session.options(api) as response
    ):
        headers = response.headers

        # confirm the CORS headers exist.
        assert "Access-Control-Allow-Headers" in headers, "Allowed headers not in headers when CORS is enabled."
        assert "Access-Control-Allow-Methods" in headers, "Allowed methods not present in headers when CORS is enabled."
        assert "Access-Control-Allow-Origin" in headers, "Allowed origin not present in headers when CORS is enabled."

        # confirm that the following methods must be provided by the server.
        expected_allowed_methods = {"GET", "OPTIONS", "POST", "DELETE", "PUT"}
        actual_allowed_methods = {method.strip() for method in headers["Access-Control-Allow-Methods"].split(",")}
        assert actual_allowed_methods == expected_allowed_methods, "Actual allowed methods not a subset of expected allowed methods."

        # confirm that the server allows any origin.
        expected_allowed_origin = "*"
        actual_allowed_origin = headers["Access-Control-Allow-Origin"].strip()
        assert actual_allowed_origin == expected_allowed_origin, "CORS allowed origin does not match."

    # check logs for errors
    expect_no_error_logs(environment)
