"""
Plugin registry integration tests.
"""

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
from lanraragi.models.archive import GetArchiveMetadataRequest
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
    InstallPluginRequest,
    UpdatePluginConfigRequest,
    UpdateRegistryRequest,
)

from aio_lanraragi_tests.common import DEFAULT_API_KEY, DEFAULT_LRR_PASSWORD
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
)

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def resource_prefix(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    yield request.config.getoption("--resource-prefix") + "test_"


@pytest.fixture
def port_offset(request: pytest.FixtureRequest) -> Generator[int, None, None]:
    yield request.config.getoption("--port-offset") + 10


@pytest.fixture
def environment(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int):
    env: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    request.session.lrr_environments = {resource_prefix: env}
    yield env
    env.teardown(remove_data=True)


@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
    client = environment.lrr_client()
    client.update_api_key(DEFAULT_API_KEY)
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_registry_crud(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test registry CRUD operations with REG_ pattern.

    1. List registries when none configured.
    2. Create a git registry, verify ID returned.
    3. Get registry by ID, verify fields.
    4. Update registry name, verify no index cleared.
    5. Delete registry by ID, verify list is empty.
    6. Create a local registry, verify fields.
    """
    environment.setup(with_api_key=True)

    # >>>>> LIST EMPTY >>>>>
    response, error = await lrr_client.misc_api.list_registries()
    assert not error, f"Failed to list registries (status {error.status}): {error.error}"
    assert len(response.registries) == 0, f"Expected empty list, got: {response.registries}"
    # <<<<< LIST EMPTY <<<<<

    # >>>>> CREATE GIT REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo plugins",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id
    assert reg_id.startswith("REG_"), f"Expected REG_ prefix, got: {reg_id}"
    assert len(reg_id) == 14, f"Expected 14 char ID, got {len(reg_id)}: {reg_id}"
    assert response.registry.name == "demo plugins"
    assert response.registry.type == "git"
    assert response.registry.url == "https://github.com/psilabs-dev/lrr-plugins-demo.git"
    # <<<<< CREATE GIT REGISTRY <<<<<

    # >>>>> GET BY ID >>>>>
    response, error = await lrr_client.misc_api.get_registry(reg_id)
    assert not error, f"Failed to get registry (status {error.status}): {error.error}"
    assert response.registry.name == "demo plugins"
    assert response.registry.type == "git"
    assert response.registry.url == "https://github.com/psilabs-dev/lrr-plugins-demo.git"
    assert response.registry.ref == "main"
    # <<<<< GET BY ID <<<<<

    # >>>>> UPDATE NAME ONLY >>>>>
    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(name="renamed plugins")
    )
    assert not error, f"Failed to update registry (status {error.status}): {error.error}"
    assert response.registry.name == "renamed plugins"
    assert response.index_cleared is False, "Name-only update should not clear index"
    # <<<<< UPDATE NAME ONLY <<<<<

    # >>>>> DELETE >>>>>
    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.list_registries()
    assert not error, f"Failed to list registries after delete (status {error.status}): {error.error}"
    assert len(response.registries) == 0, f"Expected empty list after delete, got: {response.registries}"
    # <<<<< DELETE <<<<<

    # >>>>> CREATE LOCAL REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="local plugins", type="local", path="/home/koyomi/plugins")
    )
    assert not error, f"Failed to create local registry (status {error.status}): {error.error}"
    assert response.registry.type == "local"
    assert response.registry.path == "/home/koyomi/plugins"
    local_reg_id = response.id

    response, error = await lrr_client.misc_api.delete_registry(local_reg_id)
    assert not error, f"Failed to delete local registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.list_registries()
    assert not error, f"Failed to list registries after local delete (status {error.status}): {error.error}"
    assert len(response.registries) == 0, f"Expected empty list after local delete, got: {response.registries}"
    # <<<<< CREATE LOCAL REGISTRY <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_registry_create_validation(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test registry create validation rejects invalid configurations.

    1. Create git registry without url, expect error.
    2. Create local registry without path, expect error.
    3. Create git registry with HTTP url, expect error.
    4. Create registry without name, expect error.
    5. Create a valid registry, then create a second, expect single-registry limit error.
    """
    environment.setup(with_api_key=True)

    # >>>>> MISSING URL FOR GIT >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="bad git", type="git")
    )
    assert error is not None, "Expected error for git registry without url"
    assert error.status == 400, f"Expected 400 for git registry without url, got {error.status}"
    # <<<<< MISSING URL FOR GIT <<<<<

    # >>>>> MISSING PATH FOR LOCAL >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="bad local", type="local")
    )
    assert error is not None, "Expected error for local registry without path"
    assert error.status == 400, f"Expected 400 for local registry without path, got {error.status}"
    # <<<<< MISSING PATH FOR LOCAL <<<<<

    # >>>>> NON-HTTPS URL >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="http git", type="git", provider="github", url="http://github.com/owner/repo.git")
    )
    assert error is not None, "Expected error for non-HTTPS git URL"
    assert error.status == 400, f"Expected 400 for non-HTTPS git URL, got {error.status}"
    # <<<<< NON-HTTPS URL <<<<<

    # >>>>> MISSING NAME >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="", type="local", path="/tmp/plugins")
    )
    assert error is not None, "Expected error for missing registry name"
    assert error.status == 400, f"Expected 400 for missing registry name, got {error.status}"
    # <<<<< MISSING NAME <<<<<

    # >>>>> SINGLE-REGISTRY LIMIT >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="first", type="local", path="/tmp/plugins")
    )
    assert not error, f"Failed to create first registry (status {error.status}): {error.error}"
    first_id = response.id

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="second", type="local", path="/tmp/other")
    )
    assert error is not None, "Expected error for single-registry limit"
    assert error.status == 400, f"Expected 400 for single-registry limit, got {error.status}"

    response, error = await lrr_client.misc_api.delete_registry(first_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"
    # <<<<< SINGLE-REGISTRY LIMIT <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_registry_error_paths(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test error responses for get, update, and delete on nonexistent registries.

    1. Get nonexistent registry, expect 404.
    2. Update nonexistent registry, expect 404.
    3. Delete nonexistent registry, expect 404.
    4. Create registry, update with empty body, expect error.
    5. Update with non-HTTPS url, expect error.
    6. Update ref field, verify index_cleared.
    """
    environment.setup(with_api_key=True)

    fake_id = "REG_0000000001"

    # >>>>> GET NONEXISTENT >>>>>
    response, error = await lrr_client.misc_api.get_registry(fake_id)
    assert error is not None, "Expected error for nonexistent registry"
    assert error.status == 404, f"Expected 404, got {error.status}"
    # <<<<< GET NONEXISTENT <<<<<

    # >>>>> UPDATE NONEXISTENT >>>>>
    response, error = await lrr_client.misc_api.update_registry(
        fake_id, UpdateRegistryRequest(name="nope")
    )
    assert error is not None, "Expected error updating nonexistent registry"
    assert error.status == 404, f"Expected 404 for update nonexistent, got {error.status}"
    # <<<<< UPDATE NONEXISTENT <<<<<

    # >>>>> DELETE NONEXISTENT >>>>>
    response, error = await lrr_client.misc_api.delete_registry(fake_id)
    assert error is not None, "Expected error deleting nonexistent registry"
    assert error.status == 404, f"Expected 404 for delete nonexistent, got {error.status}"
    # <<<<< DELETE NONEXISTENT <<<<<

    # >>>>> EMPTY UPDATE >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="test", type="local", path="/tmp/plugins")
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest()
    )
    assert error is not None, "Expected error for empty update body"
    assert error.status == 400, f"Expected 400 for empty update body, got {error.status}"
    # <<<<< EMPTY UPDATE <<<<<

    # >>>>> NON-HTTPS URL ON UPDATE >>>>>
    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(type="git", url="http://example.com/repo.git")
    )
    assert error is not None, "Expected error for non-HTTPS URL on update"
    assert error.status == 400, f"Expected 400 for non-HTTPS URL on update, got {error.status}"
    # <<<<< NON-HTTPS URL ON UPDATE <<<<<

    # >>>>> UPDATE REF CLEARS INDEX >>>>>
    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create git registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(ref="dev")
    )
    assert not error, f"Failed to update ref (status {error.status}): {error.error}"
    assert response.index_cleared is True, "Ref change should clear index"

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"
    # <<<<< UPDATE REF CLEARS INDEX <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_registry_update_relink(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that updating source fields clears the cached index.

    1. Create a git registry and refresh.
    2. Install a plugin from the registry.
    3. Update the URL, verify index_cleared is true.
    4. Verify installed plugin retains provenance despite index clear.
    5. Update name only, verify index_cleared is false.
    6. Switch type from git to local, verify stale git fields are absent.
    """
    environment.setup(with_api_key=True)

    # >>>>> CREATE AND REFRESH >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    assert response.index is not None, "Expected index after refresh"
    # <<<<< CREATE AND REFRESH <<<<<

    # >>>>> INSTALL PLUGIN BEFORE SOURCE CHANGE >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    assert response.registry == reg_id
    # <<<<< INSTALL PLUGIN BEFORE SOURCE CHANGE <<<<<

    # >>>>> UPDATE URL (SOURCE CHANGE) >>>>>
    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(url="https://github.com/example/other-repo.git")
    )
    assert not error, f"Failed to update registry (status {error.status}): {error.error}"
    assert response.index_cleared is True, "URL change should clear index"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins after source change (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-downloader":
            assert plugin.registry == reg_id, f"Expected provenance {reg_id} after source change, got: {plugin.registry}"
            break
    else:
        pytest.fail("Installed plugin should survive registry source change")
    # <<<<< UPDATE URL (SOURCE CHANGE) <<<<<

    # >>>>> UPDATE NAME ONLY >>>>>
    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(name="renamed")
    )
    assert not error, f"Failed to update registry name (status {error.status}): {error.error}"
    assert response.index_cleared is False, "Name change should not clear index"
    # <<<<< UPDATE NAME ONLY <<<<<

    # >>>>> TYPE SWITCH: GIT -> LOCAL >>>>>
    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(type="local", path="/tmp/plugins")
    )
    assert not error, f"Failed to switch type (status {error.status}): {error.error}"
    assert response.index_cleared is True, "Type change should clear index"
    assert response.registry.type == "local", "Type should be local"
    assert response.registry.path == "/tmp/plugins", "Path should be set"
    assert response.registry.url is None, "Stale git field 'url' should be absent"
    assert response.registry.provider is None, "Stale git field 'provider' should be absent"
    assert response.registry.ref is None, "Stale git field 'ref' should be absent"
    # <<<<< TYPE SWITCH: GIT -> LOCAL <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_registry_refresh(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test refreshing the registry index.

    1. Refresh nonexistent registry, expect error.
    2. Create registry and refresh, verify index returned with plugins.
    3. Delete registry, verify refresh fails.
    """
    environment.setup(with_api_key=True)

    # >>>>> REFRESH NONEXISTENT >>>>>
    response, error = await lrr_client.misc_api.refresh_registry("REG_0000000000")
    assert error is not None, "Expected error when refreshing nonexistent registry"
    assert error.status == 404, f"Expected 404 for refresh nonexistent, got {error.status}"
    # <<<<< REFRESH NONEXISTENT <<<<<

    # >>>>> CREATE AND REFRESH >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    assert response.index is not None, "Expected index in refresh response"
    assert response.index.get("version") is not None, "Expected version in index"
    plugins = response.index.get("plugins", {})
    assert len(plugins) > 0, "Expected at least one plugin in index"
    assert "sample-downloader" in plugins, f"Expected sample-downloader in plugins, got: {list(plugins.keys())}"
    # <<<<< CREATE AND REFRESH <<<<<

    # >>>>> DELETE CLEARS INDEX >>>>>
    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected error refreshing after registry deleted"
    assert error.status == 404, f"Expected 404 for refresh after delete, got {error.status}"
    # <<<<< DELETE CLEARS INDEX <<<<<


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_install_and_uninstall(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test installing and uninstalling a plugin, including error paths.

    1. Create registry and refresh index.
    2. Install sample-downloader plugin, verify provenance.
    3. Verify plugin appears in plugin list.
    4. Uninstall the plugin, verify absent.
    5. Uninstall again (no install path), expect error.
    6. Uninstall a namespace that was never installed, expect error.
    7. Uninstall a built-in plugin, expect 403 error.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL PLUGIN >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    assert response.namespace == "sample-downloader"
    assert response.name == "Sample Downloader"
    assert response.registry == reg_id, f"Expected provenance {reg_id}, got: {response.registry}"
    # <<<<< INSTALL PLUGIN <<<<<

    # >>>>> VERIFY INSTALLED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert "sample-downloader" in namespaces, f"Installed plugin not found in list: {namespaces}"
    # <<<<< VERIFY INSTALLED <<<<<

    # >>>>> UNINSTALL PLUGIN >>>>>
    response, error = await lrr_client.misc_api.uninstall_plugin("sample-downloader")
    assert not error, f"Failed to uninstall plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins after uninstall (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert "sample-downloader" not in namespaces, f"Plugin still listed after uninstall: {namespaces}"
    # <<<<< UNINSTALL PLUGIN <<<<<

    # >>>>> UNINSTALL AGAIN (NO INSTALL PATH) >>>>>
    response, error = await lrr_client.misc_api.uninstall_plugin("sample-downloader")
    assert error is not None, "Expected error uninstalling plugin with no install path"
    assert error.status == 404, f"Expected 404 for uninstall without install path, got {error.status}"
    # <<<<< UNINSTALL AGAIN (NO INSTALL PATH) <<<<<

    # >>>>> UNINSTALL NEVER-INSTALLED >>>>>
    response, error = await lrr_client.misc_api.uninstall_plugin("nonexistent-plugin-xyz")
    assert error is not None, "Expected error uninstalling never-installed plugin"
    assert error.status == 404, f"Expected 404 for never-installed plugin, got {error.status}"
    # <<<<< UNINSTALL NEVER-INSTALLED <<<<<

    # >>>>> UNINSTALL BUILT-IN BLOCKED >>>>>
    response, error = await lrr_client.misc_api.uninstall_plugin("copytags")
    assert error is not None, "Expected error uninstalling built-in plugin"
    assert error.status == 403, f"Expected 403 for built-in uninstall, got {error.status}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert "copytags" in namespaces, "Built-in plugin should still be listed after blocked uninstall"
    # <<<<< UNINSTALL BUILT-IN BLOCKED <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_install_error_paths(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test install error responses for invalid registry, missing index, and unknown namespace.

    1. Install from nonexistent registry, expect 404.
    2. Create registry without refresh, install, expect 409.
    3. Refresh, then install nonexistent namespace, expect 404.
    """
    environment.setup(with_api_key=True)

    # >>>>> INSTALL FROM NONEXISTENT REGISTRY >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry="REG_0000000001")
    )
    assert error is not None, "Expected error for nonexistent registry"
    assert error.status == 404, f"Expected 404 for nonexistent registry, got {error.status}"
    # <<<<< INSTALL FROM NONEXISTENT REGISTRY <<<<<

    # >>>>> INSTALL WITHOUT REFRESH >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id)
    )
    assert error is not None, "Expected error when installing without refresh"
    assert error.status == 409, f"Expected 409 for no cached index, got {error.status}"
    # <<<<< INSTALL WITHOUT REFRESH <<<<<

    # >>>>> INSTALL NONEXISTENT NAMESPACE >>>>>
    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="does-not-exist-xyz", registry=reg_id)
    )
    assert error is not None, "Expected error for nonexistent namespace"
    assert error.status == 404, f"Expected 404 for unknown namespace, got {error.status}"
    # <<<<< INSTALL NONEXISTENT NAMESPACE <<<<<

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_uninstall_reinstall(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test uninstall/reinstall lifecycle and orphaned provenance.

    1. Create registry and refresh index.
    2. Install title-suffix-1, verify managed provenance.
    3. Uninstall, verify plugin absent from list.
    4. Reinstall, verify managed provenance preserved.
    5. Enable plugin, upload archive, verify title mutated.
    6. Delete registry, verify plugin still listed with orphaned provenance.
    7. Upload another archive, verify orphaned plugin still auto-executes.
    8. Uninstall orphaned plugin, verify success.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="title-suffix-1", registry=reg_id)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    assert response.registry == reg_id, f"Expected provenance {reg_id}, got: {response.registry}"
    # <<<<< INSTALL <<<<<

    # >>>>> VERIFY INSTALLED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "title-suffix-1":
            assert plugin.registry == reg_id, f"Expected managed provenance {reg_id}, got: {plugin.registry}"
            break
    else:
        pytest.fail("title-suffix-1 not found after install")
    # <<<<< VERIFY INSTALLED <<<<<

    # >>>>> UNINSTALL >>>>>
    response, error = await lrr_client.misc_api.uninstall_plugin("title-suffix-1")
    assert not error, f"Failed to uninstall plugin (status {error.status}): {error.error}"
    # <<<<< UNINSTALL <<<<<

    # >>>>> VERIFY REMOVED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after uninstall (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert "title-suffix-1" not in namespaces, f"Plugin still in list after uninstall: {namespaces}"
    # <<<<< VERIFY REMOVED <<<<<

    # >>>>> REINSTALL >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="title-suffix-1", registry=reg_id)
    )
    assert not error, f"Failed to reinstall plugin (status {error.status}): {error.error}"
    assert response.registry == reg_id, f"Expected provenance on reinstall {reg_id}, got: {response.registry}"
    # <<<<< REINSTALL <<<<<

    # >>>>> VERIFY REINSTALLED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after reinstall (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "title-suffix-1":
            assert plugin.registry == reg_id, f"Expected managed provenance {reg_id}, got: {plugin.registry}"
            break
    else:
        pytest.fail("title-suffix-1 not found after reinstall")
    # <<<<< VERIFY REINSTALLED <<<<<

    # >>>>> ENABLE AND VERIFY EXECUTION >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "title-suffix-1", UpdatePluginConfigRequest(enabled=True)
    )
    assert not error, f"Failed to enable plugin (status {error.status}): {error.error}"

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_reinstall_exec", num_pages=1)
        response, error = await upload_archive(
            lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
            title="base", tags="test:reinstall",
        )
    assert not error, f"Upload failed (status {error.status}): {error.error}"
    arcid = response.arcid

    response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    assert not error, f"Failed to get metadata (status {error.status}): {error.error}"
    assert response.title == "base-1", f"Expected 'base-1' after enabled plugin execution, got: {response.title!r}"
    # <<<<< ENABLE AND VERIFY EXECUTION <<<<<

    # >>>>> ORPHANED PROVENANCE >>>>>
    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after registry delete (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "title-suffix-1":
            assert plugin.registry == reg_id, f"Expected orphaned provenance {reg_id}, got: {plugin.registry}"
            break
    else:
        pytest.fail("title-suffix-1 should still be listed after registry delete")
    # <<<<< ORPHANED PROVENANCE <<<<<

    # >>>>> ORPHANED PLUGIN STILL EXECUTES >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_orphan_exec", num_pages=1)
        response, error = await upload_archive(
            lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
            title="orphan", tags="test:orphan",
        )
    assert not error, f"Upload failed (status {error.status}): {error.error}"
    arcid = response.arcid

    response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    assert not error, f"Failed to get metadata (status {error.status}): {error.error}"
    assert response.title == "orphan-1", f"Expected 'orphan-1' from orphaned plugin, got: {response.title!r}"
    # <<<<< ORPHANED PLUGIN STILL EXECUTES <<<<<

    # >>>>> UNINSTALL ORPHANED >>>>>
    response, error = await lrr_client.misc_api.uninstall_plugin("title-suffix-1")
    assert not error, f"Failed to uninstall orphaned plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after orphaned uninstall (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert "title-suffix-1" not in namespaces, f"Orphaned plugin still listed after uninstall: {namespaces}"
    # <<<<< UNINSTALL ORPHANED <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_hide_unhide(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test hiding/unhiding a plugin and config reset on uninstall/reinstall.

    1. Install a plugin from the registry.
    2. Hide the plugin, verify hidden field is true.
    3. Unhide the plugin, verify hidden field is false.
    4. Hide again, set priority, uninstall, reinstall.
    5. Verify hidden and priority survive uninstall/reinstall.
    6. Hide a built-in plugin, verify hidden in plugin list, then unhide.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP AND INSTALL >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    # <<<<< SETUP AND INSTALL <<<<<

    # >>>>> HIDE PLUGIN >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "sample-metadata", UpdatePluginConfigRequest(hidden=True)
    )
    assert not error, f"Failed to update plugin config (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-metadata":
            assert plugin.hidden is True, f"Expected hidden=True, got {plugin.hidden}"
            break
    else:
        pytest.fail("Plugin sample-metadata not found in list after hide")
    # <<<<< HIDE PLUGIN <<<<<

    # >>>>> UNHIDE PLUGIN >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "sample-metadata", UpdatePluginConfigRequest(hidden=False)
    )
    assert not error, f"Failed to update plugin config (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-metadata":
            assert plugin.hidden is False, f"Expected hidden=False, got {plugin.hidden}"
            break
    else:
        pytest.fail("Plugin sample-metadata not found in list after unhide")
    # <<<<< UNHIDE PLUGIN <<<<<

    # >>>>> CONFIG SURVIVES UNINSTALL/REINSTALL >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "sample-metadata", UpdatePluginConfigRequest(hidden=True, priority=7)
    )
    assert not error, f"Failed to set hidden+priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.uninstall_plugin("sample-metadata")
    assert not error, f"Failed to uninstall plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id)
    )
    assert not error, f"Failed to reinstall plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after reinstall (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-metadata":
            assert plugin.hidden is True, f"Expected hidden=True preserved after reinstall, got {plugin.hidden}"
            assert plugin.priority == 7, f"Expected priority=7 preserved after reinstall, got {plugin.priority}"
            break
    else:
        pytest.fail("sample-metadata not found after reinstall")
    # <<<<< CONFIG SURVIVES UNINSTALL/REINSTALL <<<<<

    # >>>>> HIDE BUILT-IN PLUGIN >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "copytags", UpdatePluginConfigRequest(hidden=True)
    )
    assert not error, f"Failed to hide built-in plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "copytags":
            assert plugin.hidden is True, f"Expected built-in hidden=True, got {plugin.hidden}"
            break
    else:
        pytest.fail("Built-in plugin copytags not found in list after hide")

    response, error = await lrr_client.misc_api.update_plugin_config(
        "copytags", UpdatePluginConfigRequest(hidden=False)
    )
    assert not error, f"Failed to unhide built-in plugin (status {error.status}): {error.error}"
    # <<<<< HIDE BUILT-IN PLUGIN <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_priority(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test plugin priority via update_plugin_config.

    1. Create registry, refresh, install sample-metadata.
    2. Verify default priority is 0.
    3. Set priority to 5, verify it persists in plugin list.
    4. Set distinct priorities on sample-metadata and a default metadata plugin, verify both.
    5. Set priority on a non-metadata plugin, verify it is stored.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP AND INSTALL >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id)
    )
    assert not error, f"Failed to install sample-metadata (status {error.status}): {error.error}"
    # <<<<< SETUP AND INSTALL <<<<<

    # >>>>> VERIFY DEFAULT PRIORITY >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-metadata":
            assert plugin.priority == 0, f"Expected default priority 0, got {plugin.priority}"
            break
    else:
        pytest.fail("sample-metadata not found in plugin list")
    # <<<<< VERIFY DEFAULT PRIORITY <<<<<

    # >>>>> SET PRIORITY >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "sample-metadata", UpdatePluginConfigRequest(priority=5)
    )
    assert not error, f"Failed to set priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-metadata":
            assert plugin.priority == 5, f"Expected priority 5, got {plugin.priority}"
            break
    else:
        pytest.fail("sample-metadata not found in plugin list after priority set")
    # <<<<< SET PRIORITY <<<<<

    # >>>>> DISTINCT PRIORITIES ON TWO METADATA PLUGINS >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "copytags", UpdatePluginConfigRequest(priority=3)
    )
    assert not error, f"Failed to set copytags priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    priorities = {}
    for plugin in response.plugins:
        if plugin.namespace in ("sample-metadata", "copytags"):
            priorities[plugin.namespace] = plugin.priority
    assert priorities["sample-metadata"] == 5, f"Expected sample-metadata priority 5, got {priorities.get('sample-metadata')}"
    assert priorities["copytags"] == 3, f"Expected copytags priority 3, got {priorities.get('copytags')}"
    # <<<<< DISTINCT PRIORITIES ON TWO METADATA PLUGINS <<<<<

    # >>>>> PRIORITY ON NON-METADATA PLUGIN >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id)
    )
    assert not error, f"Failed to install sample-downloader (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_plugin_config(
        "sample-downloader", UpdatePluginConfigRequest(priority=2)
    )
    assert not error, f"Failed to set sample-downloader priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list download plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-downloader":
            assert plugin.priority == 2, f"Expected sample-downloader priority 2, got {plugin.priority}"
            break
    else:
        pytest.fail("sample-downloader not found in download plugin list")
    # <<<<< PRIORITY ON NON-METADATA PLUGIN <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_priority_execution_order(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that enabled metadata plugins execute in priority order on archive upload.

    1. Create registry, refresh, install title-suffix-1, title-suffix-2, title-suffix-3.
    2. Set priorities: suffix-2=1, suffix-1=2, suffix-3=3 (execution order: 2, 1, 3).
    3. Enable all three via Redis.
    4. Upload archive with title "test", verify final title is "test-2-1-3".
    5. Change priorities: suffix-3=1, suffix-2=2, suffix-1=3 (execution order: 3, 2, 1).
    6. Upload another archive, verify final title is "test-3-2-1".
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL ALL THREE >>>>>
    for ns in ("title-suffix-1", "title-suffix-2", "title-suffix-3"):
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace=ns, registry=reg_id)
        )
        assert not error, f"Failed to install {ns} (status {error.status}): {error.error}"
    # <<<<< INSTALL ALL THREE <<<<<

    # >>>>> SET PRIORITIES: 2, 1, 3 >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "title-suffix-2", UpdatePluginConfigRequest(priority=1)
    )
    assert not error, f"Failed to set suffix-2 priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_plugin_config(
        "title-suffix-1", UpdatePluginConfigRequest(priority=2)
    )
    assert not error, f"Failed to set suffix-1 priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_plugin_config(
        "title-suffix-3", UpdatePluginConfigRequest(priority=3)
    )
    assert not error, f"Failed to set suffix-3 priority (status {error.status}): {error.error}"
    # <<<<< SET PRIORITIES <<<<<

    # >>>>> ENABLE ALL THREE >>>>>
    for ns in ("title-suffix-1", "title-suffix-2", "title-suffix-3"):
        response, error = await lrr_client.misc_api.update_plugin_config(
            ns, UpdatePluginConfigRequest(enabled=True)
        )
        assert not error, f"Failed to enable {ns} (status {error.status}): {error.error}"
    # <<<<< ENABLE ALL THREE <<<<<

    # >>>>> UPLOAD AND VERIFY ORDER 2-1-3 >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_priority_order_1", num_pages=1)
        response, error = await upload_archive(
            lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
            title="test", tags="test:priority",
        )
    assert not error, f"Upload failed (status {error.status}): {error.error}"
    arcid = response.arcid

    response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    assert not error, f"Failed to get metadata (status {error.status}): {error.error}"
    assert response.title == "test-2-1-3", f"Expected 'test-2-1-3', got: {response.title!r}"
    # <<<<< UPLOAD AND VERIFY ORDER 2-1-3 <<<<<

    # >>>>> CHANGE PRIORITIES: 3, 2, 1 >>>>>
    response, error = await lrr_client.misc_api.update_plugin_config(
        "title-suffix-3", UpdatePluginConfigRequest(priority=1)
    )
    assert not error, f"Failed to set suffix-3 priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_plugin_config(
        "title-suffix-2", UpdatePluginConfigRequest(priority=2)
    )
    assert not error, f"Failed to set suffix-2 priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_plugin_config(
        "title-suffix-1", UpdatePluginConfigRequest(priority=3)
    )
    assert not error, f"Failed to set suffix-1 priority (status {error.status}): {error.error}"
    # <<<<< CHANGE PRIORITIES <<<<<

    # >>>>> UPLOAD AND VERIFY ORDER 3-2-1 >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_priority_order_2", num_pages=1)
        response, error = await upload_archive(
            lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
            title="test", tags="test:priority2",
        )
    assert not error, f"Upload failed (status {error.status}): {error.error}"
    arcid = response.arcid

    response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    assert not error, f"Failed to get metadata (status {error.status}): {error.error}"
    assert response.title == "test-3-2-1", f"Expected 'test-3-2-1', got: {response.title!r}"
    # <<<<< UPLOAD AND VERIFY ORDER 3-2-1 <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_install_conflict(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test plugin install conflict detection and force install.

    1. Write a .pm file declaring the same namespace as sample-metadata.
    2. Setup environment with the conflicting plugin.
    3. Create registry and refresh index.
    4. Install sample-metadata, expect provenance conflict (400).
    5. Force install sample-metadata, expect namespace conflict (422).
    6. Install sample-downloader (no conflict), expect success with provenance.
    7. Reinstall sample-downloader (same-registry upgrade), expect success.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        conflict_path = Path(tmpdir) / "SampleMetadata.pm"
        conflict_path.write_text(
            'package LANraragi::Plugin::Metadata::Testing::SampleMetadata;\n'
            'sub plugin_info { return ( name => "Conflict", namespace => "sample-metadata" ); }\n'
            '1;\n'
        )
        environment.setup(
            with_api_key=True,
            plugin_paths={"Metadata": [str(conflict_path)]},
        )

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL WITH CONFLICT (NO PROVENANCE) >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id)
    )
    assert error is not None, "Expected error when installing plugin with existing sideloaded copy"
    assert error.status == 400, f"Expected 400 for provenance conflict, got {error.status}"
    assert "without provenance" in error.error, f"Expected 'without provenance' in error, got: {error.error}"
    # <<<<< INSTALL WITH CONFLICT (NO PROVENANCE) <<<<<

    # >>>>> FORCE INSTALL STILL BLOCKED BY FILESYSTEM CONFLICT >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id, force=True)
    )
    assert error is not None, "Expected error: force bypasses provenance but not filesystem namespace conflict"
    assert error.status == 422, f"Expected 422 for namespace conflict, got {error.status}"
    # <<<<< FORCE INSTALL STILL BLOCKED BY FILESYSTEM CONFLICT <<<<<

    # >>>>> INSTALL WITHOUT CONFLICT >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id)
    )
    assert not error, f"Failed to install non-conflicting plugin (status {error.status}): {error.error}"
    assert response.namespace == "sample-downloader"
    assert response.registry == reg_id, f"Expected provenance {reg_id}, got: {response.registry}"
    # <<<<< INSTALL WITHOUT CONFLICT <<<<<

    # >>>>> UPGRADE (REINSTALL) >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id)
    )
    assert not error, f"Failed to reinstall/upgrade plugin (status {error.status}): {error.error}"
    # <<<<< UPGRADE (REINSTALL) <<<<<

    expect_no_error_logs(environment, LOGGER)


# # TODO: not needed, served its purpose.
# @pytest.mark.asyncio
# @pytest.mark.dev("registry")
# async def test_plugin_config_nonexistent(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
#     """
#     Test that updating config for a nonexistent plugin returns 404.

#     1. Setup environment with API key.
#     2. Call update_plugin_config on a namespace that was never installed.
#     3. Verify the server returns an error (404).
#     """
#     environment.setup(with_api_key=True)

#     # >>>>> UPDATE NONEXISTENT PLUGIN >>>>>
#     response, error = await lrr_client.misc_api.update_plugin_config(
#         "nonexistent-plugin-xyz", UpdatePluginConfigRequest(hidden=True)
#     )
#     assert error is not None, "Expected error when updating config for nonexistent plugin"
#     assert error.status == 404, f"Expected 404 status, got: {error.status}"
#     # <<<<< UPDATE NONEXISTENT PLUGIN <<<<<

#     expect_no_error_logs(environment, LOGGER)


# # TODO: not needed, served its purpose.
# @pytest.mark.asyncio
# @pytest.mark.dev("registry")
# async def test_plugin_config_survives_restart(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
#     """
#     Test that plugin configuration persists across server restart.

#     1. Create registry, refresh, install a plugin.
#     2. Hide the plugin via update_plugin_config.
#     3. Restart the server.
#     4. Verify the plugin is still hidden after restart.
#     """
#     environment.setup(with_api_key=True)

#     # >>>>> SETUP AND INSTALL >>>>>
#     response, error = await lrr_client.misc_api.create_registry(
#         CreateRegistryRequest(
#             name="demo",
#             type="git",
#             provider="github",
#             url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
#             ref="main",
#         )
#     )
#     assert not error, f"Failed to create registry (status {error.status}): {error.error}"
#     reg_id = response.id

#     response, error = await lrr_client.misc_api.refresh_registry(reg_id)
#     assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

#     response, error = await lrr_client.misc_api.install_plugin(
#         InstallPluginRequest(namespace="sample-metadata", registry=reg_id)
#     )
#     assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
#     # <<<<< SETUP AND INSTALL <<<<<

#     # >>>>> HIDE PLUGIN >>>>>
#     response, error = await lrr_client.misc_api.update_plugin_config(
#         "sample-metadata", UpdatePluginConfigRequest(hidden=True)
#     )
#     assert not error, f"Failed to hide plugin (status {error.status}): {error.error}"

#     response, error = await lrr_client.misc_api.get_available_plugins(
#         GetAvailablePluginsRequest(type="metadata")
#     )
#     assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
#     for plugin in response.plugins:
#         if plugin.namespace == "sample-metadata":
#             assert plugin.hidden is True, f"Expected hidden=True before restart, got {plugin.hidden}"
#             break
#     else:
#         pytest.fail("Plugin sample-metadata not found before restart")
#     # <<<<< HIDE PLUGIN <<<<<

#     # >>>>> RESTART >>>>>
#     environment.restart()
#     # <<<<< RESTART <<<<<

#     # >>>>> VERIFY AFTER RESTART >>>>>
#     response, error = await lrr_client.misc_api.get_available_plugins(
#         GetAvailablePluginsRequest(type="metadata")
#     )
#     assert not error, f"Failed to list plugins after restart (status {error.status}): {error.error}"
#     for plugin in response.plugins:
#         if plugin.namespace == "sample-metadata":
#             assert plugin.hidden is True, f"Expected hidden=True after restart, got {plugin.hidden}"
#             break
#     else:
#         pytest.fail("Plugin sample-metadata not found after restart")
#     # <<<<< VERIFY AFTER RESTART <<<<<

#     expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_uninstall_ui(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test plugin install, enable, uninstall, and reinstall through the UI.

    1. Create registry, refresh index via API.
    2. Navigate to plugin page, install sample-metadata from registry.
    3. Move sample-metadata to enabled pool, save configuration.
    4. Uninstall sample-metadata, verify absent from page and API.
    5. Refresh registry, verify sample-metadata available for reinstall.
    6. Reinstall, verify managed provenance.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await bc.new_page()

            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            # >>>>> LOGIN >>>>>
            await page.goto(f"{lrr_client.lrr_base_url}/config/plugins")
            await page.wait_for_load_state("networkidle")

            if "login" in page.url.lower():
                await page.fill("#pw_field", DEFAULT_LRR_PASSWORD)
                await page.click("input[type='submit'][value='Login']")
                await page.wait_for_load_state("networkidle")
            assert "plugins" in page.url, f"Expected plugins page, got: {page.url}"
            responses.clear()
            console_evts.clear()
            # <<<<< LOGIN <<<<<

            # >>>>> INSTALL >>>>>
            # Expand the Metadata Plugins collapsible (hidden by allcollapsible on load)
            await page.locator(".collapsible-title", has_text="Metadata Plugins").click()
            await page.wait_for_timeout(500)

            await page.locator("#registry-refresh-btn").click()
            await page.wait_for_load_state("networkidle")

            sample_metadata_row = page.locator(".registry-plugin-row").filter(
                has=page.locator("h2", has_text="Sample Metadata")
            )
            async with page.expect_response("**/api/plugins/install") as response_info:
                await sample_metadata_row.locator("input[type='button']").click()
            install_response = await response_info.value
            assert install_response.ok, f"Install API failed: {install_response.status}"
            # <<<<< INSTALL <<<<<

            # >>>>> VERIFY INSTALLED >>>>>
            badge = page.locator(".plugin-card[data-namespace='sample-metadata'] .plugin-badge")
            await page.wait_for_timeout(500)
            assert await badge.text_content() == "managed", f"Expected 'managed' badge after install, got: {await badge.text_content()}"
            # <<<<< VERIFY INSTALLED <<<<<

            # >>>>> ENABLE AND SAVE >>>>>
            # native drag does not trigger SortableJS; move via DOM
            moved = await page.evaluate("""() => {
                const card = document.querySelector('.plugin-card[data-namespace="sample-metadata"]');
                const enabledPool = document.getElementById('metadata-enabled');
                if (!card || !enabledPool) return false;

                const emptyMsg = enabledPool.querySelector('.pool-empty-msg');
                if (emptyMsg) emptyMsg.remove();

                enabledPool.appendChild(card);
                if (typeof Plugins !== 'undefined' && Plugins.renumberEnabled) {
                    Plugins.renumberEnabled();
                }
                return card.closest('#metadata-enabled') !== null;
            }""")
            assert moved, "Failed to move sample-metadata to enabled pool"

            await page.get_by_role("button", name="Save Plugin Configuration").click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            # <<<<< ENABLE AND SAVE <<<<<

            # >>>>> UNINSTALL >>>>>
            await page.locator(".plugin-uninstall-btn[data-namespace='sample-metadata']").click()

            # confirm uninstall dialog; wait for DELETE response then page reload
            await page.wait_for_selector(".swal2-confirm", state="visible")
            async with page.expect_response("**/api/plugins/installed/**") as response_info:
                await page.click(".swal2-confirm")
            uninstall_response = await response_info.value
            assert uninstall_response.ok, f"Uninstall API failed: {uninstall_response.status}"
            await page.wait_for_load_state("networkidle")
            # <<<<< UNINSTALL <<<<<

            # >>>>> VERIFY REMOVED >>>>>
            card_count = await page.locator(".plugin-card[data-namespace='sample-metadata']").count()
            assert card_count == 0, f"sample-metadata still in DOM after uninstall (count: {card_count})"

            # verify via API
            response, error = await lrr_client.misc_api.get_available_plugins(
                GetAvailablePluginsRequest(type="metadata")
            )
            assert not error, f"Failed to list plugins after uninstall (status {error.status}): {error.error}"
            namespaces = {p.namespace for p in response.plugins}
            assert "sample-metadata" not in namespaces, f"Plugin still in API after uninstall: {namespaces}"
            # <<<<< VERIFY REMOVED <<<<<

            # >>>>> REFRESH AND VERIFY AVAILABLE >>>>>
            # Re-expand collapsible (page reloaded after uninstall)
            await page.locator(".collapsible-title", has_text="Metadata Plugins").click()
            await page.wait_for_timeout(500)

            await page.locator("#registry-refresh-btn").click()
            await page.wait_for_load_state("networkidle")

            reinstall_row = page.locator(".registry-plugin-row").filter(
                has=page.locator("h2", has_text="Sample Metadata")
            )
            await reinstall_row.wait_for(state="visible")
            # <<<<< REFRESH AND VERIFY AVAILABLE <<<<<

            # >>>>> REINSTALL >>>>>
            async with page.expect_response("**/api/plugins/install") as response_info:
                await reinstall_row.locator("input[type='button']").click()
            reinstall_response = await response_info.value
            assert reinstall_response.ok, f"Reinstall API failed: {reinstall_response.status}"

            badge_after = page.locator(".plugin-card[data-namespace='sample-metadata'] .plugin-badge")
            await page.wait_for_timeout(500)
            assert await badge_after.text_content() == "managed", f"Expected 'managed' after reinstall, got: {await badge_after.text_content()}"
            # <<<<< REINSTALL <<<<<

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_uninstall_not_listed(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that uninstalled plugin is absent from plugin list across repeated cycles.

    1. Create registry and refresh index.
    2. Run 5 cycles of: install sample-login, uninstall, verify absent from GET /api/plugins/login.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    for i in range(5):
        LOGGER.info(f"Cycle {i}: installing sample-login")
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace="sample-login", registry=reg_id)
        )
        assert not error, f"Cycle {i}: install failed (status {error.status}): {error.error}"

        LOGGER.info(f"Cycle {i}: uninstalling sample-login")
        response, error = await lrr_client.misc_api.uninstall_plugin("sample-login")
        assert not error, f"Cycle {i}: uninstall failed (status {error.status}): {error.error}"

        LOGGER.info(f"Cycle {i}: verifying absent from plugin list")
        response, error = await lrr_client.misc_api.get_available_plugins(
            GetAvailablePluginsRequest(type="login")
        )
        assert not error, f"Cycle {i}: list failed (status {error.status}): {error.error}"
        namespaces = {p.namespace for p in response.plugins}
        assert "sample-login" not in namespaces, f"Cycle {i}: sample-login still listed after uninstall: {namespaces}"

    expect_no_error_logs(environment, LOGGER)


# # TODO: this needs improvement.
# @pytest.mark.asyncio
# @pytest.mark.dev("registry")
# @pytest.mark.ratelimit
# async def test_sideloaded_script_replaces_managed_duplicate_without_duplicate_api_entries(
#     lrr_client: LRRClient,
#     environment: AbstractLRRDeploymentContext,
# ):
#     """
#     Test that replacing a managed script with a sideloaded duplicate does not duplicate script API entries.

#     1. Create registry, refresh index, and install sample-script.
#     2. Attempt to upload the sideloaded SampleScript.pm while managed copy exists, expect failure.
#     3. Uninstall the managed sample-script.
#     4. Upload the sideloaded SampleScript.pm, expect success.
#     5. Verify GET /api/plugins/script returns one sample-script entry.
#     """
#     plugin_path = Path(__file__).parent / "resources" / "plugins" / "scripts" / "SampleScript.pm"
#     assert plugin_path.exists(), f"Test plugin file not found: {plugin_path}"

#     environment.setup(with_api_key=True)

#     # >>>>> SETUP REGISTRY AND INSTALL MANAGED SCRIPT >>>>>
#     response, error = await lrr_client.misc_api.create_registry(
#         CreateRegistryRequest(
#             name="demo",
#             type="git",
#             provider="github",
#             url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
#             ref="main",
#         )
#     )
#     assert not error, f"Failed to create registry (status {error.status}): {error.error}"
#     reg_id = response.id

#     response, error = await lrr_client.misc_api.refresh_registry(reg_id)
#     assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

#     response, error = await lrr_client.misc_api.install_plugin(
#         InstallPluginRequest(namespace="sample-script", registry=reg_id)
#     )
#     assert not error, f"Failed to install sample-script (status {error.status}): {error.error}"
#     # <<<<< SETUP REGISTRY AND INSTALL MANAGED SCRIPT <<<<<

#     # >>>>> DUPLICATE SIDELOAD UPLOAD FAILS >>>>>
#     login_url = lrr_client.misc_api.api_context.build_url("/login")
#     upload_url = lrr_client.misc_api.api_context.build_url("/config/plugins/upload")
#     async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
#         login_form = aiohttp.FormData(quote_fields=False)
#         login_form.add_field("password", DEFAULT_LRR_PASSWORD)
#         login_form.add_field("redirect", "index")
#         async with session.post(login_url, data=login_form) as response:
#             content = await response.text()
#             assert response.status == 200, f"Expected login redirect target to resolve with 200, got {response.status}"
#             assert "LANraragi" in content, f"Expected login flow to land on app page, got: {content}"

#         with plugin_path.open("rb") as file_handle:
#             form_data = aiohttp.FormData(quote_fields=False)
#             form_data.add_field("file", file_handle, filename=plugin_path.name)
#             async with session.post(upload_url, data=form_data) as response:
#                 content = await response.text()
#                 assert response.status == 200, f"Expected 200 upload status, got {response.status}: {content}"
#                 assert '"success":0' in content, f"Expected failed duplicate upload, got: {content}"

#     response, error = await lrr_client.misc_api.get_available_plugins(
#         GetAvailablePluginsRequest(type="script")
#     )
#     assert not error, f"Failed to list scripts after duplicate upload (status {error.status}): {error.error}"
#     sample_scripts = [plugin for plugin in response.plugins if plugin.namespace == "sample-script"]
#     assert len(sample_scripts) == 1, f"Expected one sample-script before uninstall, got {len(sample_scripts)}"
#     # <<<<< DUPLICATE SIDELOAD UPLOAD FAILS <<<<<

#     # >>>>> UNINSTALL MANAGED SCRIPT >>>>>
#     response, error = await lrr_client.misc_api.uninstall_plugin("sample-script")
#     assert not error, f"Failed to uninstall sample-script (status {error.status}): {error.error}"
#     # <<<<< UNINSTALL MANAGED SCRIPT <<<<<

#     # >>>>> SIDELOAD UPLOAD SUCCEEDS >>>>>
#     async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
#         login_form = aiohttp.FormData(quote_fields=False)
#         login_form.add_field("password", DEFAULT_LRR_PASSWORD)
#         login_form.add_field("redirect", "index")
#         async with session.post(login_url, data=login_form) as response:
#             content = await response.text()
#             assert response.status == 200, f"Expected login redirect target to resolve with 200, got {response.status}"
#             assert "LANraragi" in content, f"Expected login flow to land on app page, got: {content}"

#         with plugin_path.open("rb") as file_handle:
#             form_data = aiohttp.FormData(quote_fields=False)
#             form_data.add_field("file", file_handle, filename=plugin_path.name)
#             async with session.post(upload_url, data=form_data) as response:
#                 content = await response.text()
#                 assert response.status == 200, f"Expected 200 upload status, got {response.status}: {content}"
#                 assert '"success":1' in content, f"Expected successful sideload upload, got: {content}"
#     # <<<<< SIDELOAD UPLOAD SUCCEEDS <<<<<

#     # >>>>> VERIFY SINGLE API ENTRY >>>>>
#     response, error = await lrr_client.misc_api.get_available_plugins(
#         GetAvailablePluginsRequest(type="script")
#     )
#     assert not error, f"Failed to list scripts after sideload upload (status {error.status}): {error.error}"
#     sample_scripts = [plugin for plugin in response.plugins if plugin.namespace == "sample-script"]
#     assert len(sample_scripts) == 1, f"Expected one sample-script after sideload replacement, got {len(sample_scripts)}"
#     # <<<<< VERIFY SINGLE API ENTRY <<<<<

#     expect_no_error_logs(environment, LOGGER)
