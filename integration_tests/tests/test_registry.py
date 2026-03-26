"""
Plugin registry integration tests.
"""

import logging
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
    InstallPluginRequest,
    UpdatePluginConfigRequest,
    UpdateRegistryRequest,
)

from aio_lanraragi_tests.common import DEFAULT_API_KEY
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment

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
    """
    environment.setup(with_api_key=True)

    # >>>>> MISSING URL FOR GIT >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="bad git", type="git")
    )
    assert error is not None, "Expected error for git registry without url"
    # <<<<< MISSING URL FOR GIT <<<<<

    # >>>>> MISSING PATH FOR LOCAL >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="bad local", type="local")
    )
    assert error is not None, "Expected error for local registry without path"
    # <<<<< MISSING PATH FOR LOCAL <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_registry_update_relink(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that updating source fields clears the cached index.

    1. Create a git registry and refresh.
    2. Update the URL, verify index_cleared is true.
    3. Update name only, verify index_cleared is false.
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

    # >>>>> UPDATE URL (SOURCE CHANGE) >>>>>
    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(url="https://github.com/example/other-repo.git")
    )
    assert not error, f"Failed to update registry (status {error.status}): {error.error}"
    assert response.index_cleared is True, "URL change should clear index"
    # <<<<< UPDATE URL (SOURCE CHANGE) <<<<<

    # >>>>> UPDATE NAME ONLY >>>>>
    response, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(name="renamed")
    )
    assert not error, f"Failed to update registry name (status {error.status}): {error.error}"
    assert response.index_cleared is False, "Name change should not clear index"
    # <<<<< UPDATE NAME ONLY <<<<<

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
    # <<<<< DELETE CLEARS INDEX <<<<<


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_install_and_uninstall(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test installing and uninstalling a plugin from the registry.

    1. Create registry and refresh index.
    2. Install sample-downloader plugin (sole registry fallback).
    3. Verify plugin appears in plugin list.
    4. Uninstall the plugin.
    5. Verify plugin is no longer listed.
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
        InstallPluginRequest(namespace="sample-downloader")
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
    # <<<<< UNINSTALL PLUGIN <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_hide_unhide(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test hiding and unhiding a plugin.

    1. Install a plugin from the registry.
    2. Hide the plugin, verify hidden field is true.
    3. Unhide the plugin, verify hidden field is false.
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
        InstallPluginRequest(namespace="sample-metadata")
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

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_install_conflict(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test plugin install conflict detection and upgrade behavior.

    1. Write a .pm file declaring the same namespace as sample-metadata.
    2. Setup environment with the conflicting plugin.
    3. Create registry and refresh index.
    4. Install sample-metadata, expect no-provenance error.
    5. Force install sample-metadata, expect namespace conflict error (model-layer).
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
        InstallPluginRequest(namespace="sample-metadata")
    )
    assert error is not None, "Expected error when installing plugin with existing sideloaded copy"
    assert "no provenance" in error.error, f"Expected 'no provenance' in error, got: {error.error}"
    # <<<<< INSTALL WITH CONFLICT (NO PROVENANCE) <<<<<

    # >>>>> FORCE INSTALL (NAMESPACE CONFLICT) >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-metadata", force=True)
    )
    assert error is not None, "Expected namespace conflict error on force install"
    assert "already declared" in error.error, f"Expected 'already declared' in error, got: {error.error}"
    # <<<<< FORCE INSTALL (NAMESPACE CONFLICT) <<<<<

    # >>>>> INSTALL WITHOUT CONFLICT >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader")
    )
    assert not error, f"Failed to install non-conflicting plugin (status {error.status}): {error.error}"
    assert response.namespace == "sample-downloader"
    assert response.registry == reg_id, f"Expected provenance {reg_id}, got: {response.registry}"
    # <<<<< INSTALL WITHOUT CONFLICT <<<<<

    # >>>>> UPGRADE (REINSTALL) >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader")
    )
    assert not error, f"Failed to reinstall/upgrade plugin (status {error.status}): {error.error}"
    # <<<<< UPGRADE (REINSTALL) <<<<<

    expect_no_error_logs(environment, LOGGER)
