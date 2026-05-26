import asyncio
import logging
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import aiohttp
import playwright.async_api
import playwright.async_api._generated
import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import UpdateReadingProgressionRequest
from pydantic import BaseModel, Field

from aio_lanraragi_tests.common import (
    DEFAULT_API_KEY,
    DEFAULT_LRR_PASSWORD,
    LRR_INDEX_TITLE,
    LRR_LOGIN_TITLE,
)
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    upload_archive,
)
from aio_lanraragi_tests.utils.concurrency import get_bounded_sem
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
)

LOGGER = logging.getLogger(__name__)

class ApiAuthMatrixParams(BaseModel):
    # used by test_api_auth_matrix.
    # 6-case matrix: 2 (nofunmode) x 3 (client-key-state: none / valid / invalid).
    # is_api_key_configured_server is dropped (always True under LRRRS — harness enforces).
    # is_auth_progress is dropped (covered by test_local_progress_*).
    is_nofunmode: bool
    is_api_key_configured_client: bool
    is_matching_api_key: bool = Field(..., description="Set to False if not is_api_key_configured_client")

@pytest.fixture
def resource_prefix(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    yield request.config.getoption("--resource-prefix") + "test_"

@pytest.fixture
def port_offset(request: pytest.FixtureRequest) -> Generator[int, None, None]:
    yield request.config.getoption("--port-offset") + 10

@pytest.fixture
def is_lrr_debug_mode(request: pytest.FixtureRequest) -> Generator[bool, None, None]:
    yield request.config.getoption("--lrr-debug")

@pytest.fixture
def environment(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int) -> Generator[AbstractLRRDeploymentContext, None, None]:
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    request.session.lrr_environment = environment

    # configure environments to session
    environments: dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

    try:
        yield environment
    finally:
        environment.teardown(remove_data=True)

@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield get_bounded_sem(on_unix=2, on_windows=1) # reduced val (we're not testing concurrency/upload).

@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
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
    is_nofunmode: bool, is_api_key_configured_client: bool,
    is_matching_api_key: bool, environment: AbstractLRRDeploymentContext, lrr_client: LRRClient,
):
    # sanity check.
    if is_matching_api_key and not is_api_key_configured_client:
        raise ValueError("is_matching_api_key requires is_api_key_configured_client.")

    # configuration stage.
    if is_nofunmode:
        environment.enable_nofun_mode()
    else:
        environment.disable_nofun_mode()
    environment.update_api_key(DEFAULT_API_KEY)
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
        """
        require_valid_api_key = is_api_key_configured_client and is_matching_api_key
        if endpoint_is_public:
            return True if not is_nofunmode else require_valid_api_key
        else:
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

    # check logs for errors
    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_ui_nofunmode_login_right_password(environment: AbstractLRRDeploymentContext, is_lrr_debug_mode: bool, lrr_client: LRRClient):
    """
    Login with correct password.
    """
    environment.setup(with_nofunmode=True, lrr_debug_mode=is_lrr_debug_mode)

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()

            # capture all network and console traffic
            responses: list[playwright.async_api._generated.Response] = []
            page.on("response", lambda response: responses.append(response))

            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_LOGIN_TITLE

            # right password test
            await page.fill("#pw_field", DEFAULT_LRR_PASSWORD)
            await page.click("input[type='submit'][value='Login']")
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_INDEX_TITLE

            # check browser traffic is OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
        finally:
            await bc.close()
            await browser.close()

    # check logs for errors
    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_ui_nofunmode_login_empty_password(environment: AbstractLRRDeploymentContext, is_lrr_debug_mode: bool, lrr_client: LRRClient):
    """
    Login without password.
    """
    environment.setup(with_nofunmode=True, lrr_debug_mode=is_lrr_debug_mode)

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()

            # capture all network and console traffic
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_LOGIN_TITLE

            # empty password test
            await page.click("input[type='submit'][value='Login']")
            await page.wait_for_load_state("networkidle")
            assert "Wrong Password." in await page.content()
            assert await page.title() == LRR_LOGIN_TITLE

            # check browser traffic is OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()

    # check logs for errors
    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_ui_nofunmode_login_wrong_password(environment: AbstractLRRDeploymentContext, is_lrr_debug_mode: bool, lrr_client: LRRClient):
    """
    Login with wrong password.
    """
    environment.setup(with_nofunmode=True, lrr_debug_mode=is_lrr_debug_mode)

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()

            # capture all network and console traffic
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_LOGIN_TITLE

            # wrong password test
            await page.fill("#pw_field", "password")
            await page.click("input[type='submit'][value='Login']")
            await page.wait_for_load_state("networkidle")
            assert "Wrong Password." in await page.content()
            assert await page.title() == LRR_LOGIN_TITLE

            # check browser traffic is OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()

    # check logs for errors
    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
@pytest.mark.playwright
async def test_ui_enable_nofunmode(environment: AbstractLRRDeploymentContext, is_lrr_debug_mode: bool, lrr_client: LRRClient):
    """
    Simulate UI: enable nofunmode and check that login is enforced.
    """
    environment.setup(with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()

            # capture all network and console traffic
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

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
            await page.wait_for_load_state("networkidle")
            LOGGER.info("Clicking security settings.")
            await page.get_by_text("Security").click()
            await page.wait_for_timeout(300)
            LOGGER.info("Enabling No-Fun Mode.")
            nofun_checkbox = page.get_by_role("checkbox", name="Enabling No-Fun Mode will")
            await nofun_checkbox.wait_for(state="visible", timeout=5000)
            await nofun_checkbox.check()
            LOGGER.info("Clicking save settings.")
            await page.get_by_role("button", name="Save Settings").click()
            await page.wait_for_load_state("networkidle")

            # check browser traffic is OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()

    environment.restart()

    LOGGER.info("Checking that LRR server is locked after restart.")
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await browser.new_page()

            # capture all network and console traffic
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(lrr_client.lrr_base_url)
            await page.wait_for_load_state("networkidle")
            assert await page.title() == LRR_LOGIN_TITLE

            # check browser traffic is OK.
            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()

    # check logs for errors
    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
async def test_api_auth_matrix(
    environment: AbstractLRRDeploymentContext, lrr_client: LRRClient, is_lrr_debug_mode: bool
):
    """
    Test auth enforcement across 6 sub-cases: 2 (nofunmode) x 3 (client-key-state: none / valid / invalid).

    Server API key is always set (harness enforces this under LRRRS).
    Progress endpoint auth is covered separately by test_local_progress_*.

    sample public endpoints:
    - GET /api/archives
    - GET /api/categories
    - GET /api/info

    sample protected endpoints:
    - GET /api/shinobu
    - GET /api/database/backup
    """
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # 6 cases: 2 (nofunmode) x 3 (client-key-state: none / valid / invalid).
    test_params: list[ApiAuthMatrixParams] = [
        ApiAuthMatrixParams(is_nofunmode=False, is_api_key_configured_client=False, is_matching_api_key=False),
        ApiAuthMatrixParams(is_nofunmode=False, is_api_key_configured_client=True, is_matching_api_key=True),
        ApiAuthMatrixParams(is_nofunmode=False, is_api_key_configured_client=True, is_matching_api_key=False),
        ApiAuthMatrixParams(is_nofunmode=True, is_api_key_configured_client=False, is_matching_api_key=False),
        ApiAuthMatrixParams(is_nofunmode=True, is_api_key_configured_client=True, is_matching_api_key=True),
        ApiAuthMatrixParams(is_nofunmode=True, is_api_key_configured_client=True, is_matching_api_key=False),
    ]
    num_tests = len(test_params)

    for i, test_param in enumerate(test_params):
        LOGGER.info(f"Test configuration ({i+1}/{num_tests}): is_nofunmode={test_param.is_nofunmode}, is_apikey_configured_client={test_param.is_api_key_configured_client}, is_matching_api_key={test_param.is_matching_api_key}")
        await sample_test_api_auth_matrix(
            test_param.is_nofunmode, test_param.is_api_key_configured_client,
            test_param.is_matching_api_key, environment, lrr_client
        )

@pytest.mark.asyncio
async def test_options_preflight_matrix(environment: AbstractLRRDeploymentContext, lrr_client: LRRClient, is_lrr_debug_mode: bool):
    """
    Test OPTIONS preflight across all CORS x nofun combinations.

    All combinations must return 200 — the OpenAPI plugin auto-generates OPTIONS
    routes, and nofun must not block them.

    When CORS is enabled, CORS headers must be present; when disabled, they must be absent.
    """
    environment.setup(enable_cors=False, lrr_debug_mode=is_lrr_debug_mode)
    api = lrr_client.build_url("/api/shinobu")

    for enable_cors in [False, True]:
        for with_nofun in [False, True]:
            LOGGER.debug(f"Testing preflight: cors={enable_cors}, nofun={with_nofun}.")

            if enable_cors:
                environment.enable_cors()
            else:
                environment.disable_cors()
            if with_nofun:
                environment.enable_nofun_mode()
            else:
                environment.disable_nofun_mode()
            environment.restart()

            async with (
                aiohttp.ClientSession(headers={"Origin": "https://www.example.com"}) as session,
                session.options(api) as response
            ):
                headers = response.headers
                label = f"cors={enable_cors}, nofun={with_nofun}"

                assert response.status == 200, f"Expected 200 for OPTIONS preflight ({label}), got {response.status}"

                if enable_cors:
                    assert "Access-Control-Allow-Headers" in headers, f"Missing Allow-Headers ({label})."
                    assert "Access-Control-Allow-Methods" in headers, f"Missing Allow-Methods ({label})."
                    assert "Access-Control-Allow-Origin" in headers, f"Missing Allow-Origin ({label})."

                    expected_allowed_methods = {"GET", "OPTIONS", "POST", "DELETE", "PUT"}
                    actual_allowed_methods = {m.strip() for m in headers["Access-Control-Allow-Methods"].split(",")}
                    assert actual_allowed_methods == expected_allowed_methods, f"Allowed methods mismatch ({label})."

                    assert headers["Access-Control-Allow-Origin"].strip() == "*", f"Allowed origin mismatch ({label})."
                else:
                    assert "Access-Control-Allow-Headers" not in headers, f"Unexpected Allow-Headers ({label})."
                    assert "Access-Control-Allow-Methods" not in headers, f"Unexpected Allow-Methods ({label})."
                    assert "Access-Control-Allow-Origin" not in headers, f"Unexpected Allow-Origin ({label})."

    # check logs for errors
    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
async def test_local_progress_disabled(
    environment: AbstractLRRDeploymentContext, lrr_client: LRRClient,
    semaphore: asyncio.Semaphore, is_lrr_debug_mode: bool
):
    """
    Test that the progress endpoint rejects all requests when local progress is enabled
    and auth progress is disabled (localprogress=1, authprogress=0).

    In this configuration, server-side progress tracking is fully disabled;
    the frontend stores progress in localStorage instead.
    """
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)
    lrr_client.update_api_key(DEFAULT_API_KEY)

    # upload a single archive.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        save_path = create_archive_file(tmpdir, "local_progress_test", num_pages=10)
        response, error = await upload_archive(lrr_client, save_path, save_path.name, semaphore)
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid

    # enable local progress, disable auth progress, restart.
    environment.enable_local_progress()
    environment.disable_auth_progress()
    environment.restart()

    # verify server reports progress tracking as disabled.
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {error.status}): {error.error}"
    assert response.server_tracks_progress is False, "Expected server_tracks_progress=False when localprogress is enabled."
    assert response.authenticated_progress is False, "Expected authenticated_progress=False when authprogress is disabled."

    # authenticated client should be rejected with 400.
    response, error = await lrr_client.archive_api.update_reading_progression(
        UpdateReadingProgressionRequest(arcid=arcid, page=1)
    )
    assert not response, "Expected no response payload when server-side progress is disabled."
    assert error.status == 400, f"Expected status 400, got: {error.status}."

    # unauthenticated client should also be rejected with 400.
    lrr_client.update_api_key(None)
    response, error = await lrr_client.archive_api.update_reading_progression(
        UpdateReadingProgressionRequest(arcid=arcid, page=1)
    )
    assert not response, "Expected no response payload when server-side progress is disabled."
    assert error.status == 400, f"Expected status 400, got: {error.status}."

    # check logs for errors
    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
async def test_local_progress_with_auth_progress(
    environment: AbstractLRRDeploymentContext, lrr_client: LRRClient,
    semaphore: asyncio.Semaphore, is_lrr_debug_mode: bool
):
    """
    Test that auth progress overrides local progress (localprogress=1, authprogress=1).

    When both are enabled, authenticated users can still update server-side progress,
    while unauthenticated users are expected to use localStorage on the frontend.
    """
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)
    lrr_client.update_api_key(DEFAULT_API_KEY)

    # upload a single archive.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        save_path = create_archive_file(tmpdir, "local_auth_progress_test", num_pages=10)
        response, error = await upload_archive(lrr_client, save_path, save_path.name, semaphore)
        assert not error, f"Upload failed (status {error.status}): {error.error}"
        arcid = response.arcid

    # enable both local progress and auth progress, restart.
    environment.enable_local_progress()
    environment.enable_auth_progress()
    environment.restart()

    # verify server reports auth progress as enabled.
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {error.status}): {error.error}"
    assert response.authenticated_progress is True, "Expected authenticated_progress=True when authprogress is enabled."

    # authenticated client should succeed with 200.
    lrr_client.update_api_key(DEFAULT_API_KEY)
    response, error = await lrr_client.archive_api.update_reading_progression(
        UpdateReadingProgressionRequest(arcid=arcid, page=1)
    )
    assert not error, f"Progress update failed (status {error.status}): {error.error}"
    assert response.page == 1, f"Expected page=1, got: {response.page}."

    # check logs for errors
    expect_no_error_logs(environment, LOGGER)
