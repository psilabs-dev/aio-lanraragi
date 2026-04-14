"""
Plugin registry CRUD integration tests.
"""

import logging

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
    InstallPluginRequest,
    UpdateRegistryRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)

LOGGER = logging.getLogger(__name__)


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
