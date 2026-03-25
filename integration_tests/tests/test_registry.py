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
    GetAvailablePluginsRequest,
    InstallPluginRequest,
    SetRegistryRequest,
    UpdatePluginConfigRequest,
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
    Test registry configuration CRUD operations.

    1. Get registry when none is configured.
    2. Set a git registry, verify it persists.
    3. Get the registry, verify fields match.
    4. Delete the registry, verify it is removed.
    5. Set a local registry, verify it persists.
    """
    environment.setup(with_api_key=True)

    # >>>>> GET EMPTY REGISTRY >>>>>
    response, error = await lrr_client.misc_api.get_registry()
    assert not error, f"Failed to get registry (status {error.status}): {error.error}"
    assert response.registry is None, f"Expected no registry, got: {response.registry}"
    # <<<<< GET EMPTY REGISTRY <<<<<

    # >>>>> SET GIT REGISTRY >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to set registry (status {error.status}): {error.error}"
    assert response.registry is not None, "Expected registry in response"
    assert response.registry.type == "git"
    assert response.registry.url == "https://github.com/psilabs-dev/lrr-plugins-demo.git"
    assert response.registry.ref == "main"
    # <<<<< SET GIT REGISTRY <<<<<

    # >>>>> GET GIT REGISTRY >>>>>
    response, error = await lrr_client.misc_api.get_registry()
    assert not error, f"Failed to get registry (status {error.status}): {error.error}"
    assert response.registry is not None, "Expected registry after set"
    assert response.registry.type == "git"
    assert response.registry.url == "https://github.com/psilabs-dev/lrr-plugins-demo.git"
    assert response.registry.ref == "main"
    # <<<<< GET GIT REGISTRY <<<<<

    # >>>>> DELETE REGISTRY >>>>>
    response, error = await lrr_client.misc_api.delete_registry()
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_registry()
    assert not error, f"Failed to get registry after delete (status {error.status}): {error.error}"
    assert response.registry is None, f"Expected no registry after delete, got: {response.registry}"
    # <<<<< DELETE REGISTRY <<<<<

    # >>>>> SET LOCAL REGISTRY >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(type="local", path="/home/koyomi/plugins")
    )
    assert not error, f"Failed to set local registry (status {error.status}): {error.error}"
    assert response.registry is not None, "Expected registry in response"
    assert response.registry.type == "local"
    assert response.registry.path == "/home/koyomi/plugins"
    # <<<<< SET LOCAL REGISTRY <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_registry_set_validation(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test registry set validation rejects invalid configurations.

    1. Set git registry without url, expect error.
    2. Set local registry without path, expect error.
    """
    environment.setup(with_api_key=True)

    # >>>>> MISSING URL FOR GIT >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(type="git")
    )
    assert error is not None, "Expected error for git registry without url"
    # <<<<< MISSING URL FOR GIT <<<<<

    # >>>>> MISSING PATH FOR LOCAL >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(type="local")
    )
    assert error is not None, "Expected error for local registry without path"
    # <<<<< MISSING PATH FOR LOCAL <<<<<


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_registry_overwrite(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that setting a new registry overwrites the previous one.

    1. Set a git registry.
    2. Set a local registry, verify git fields are gone.
    """
    environment.setup(with_api_key=True)

    # >>>>> SET GIT REGISTRY >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(type="git", provider="github", url="https://github.com/example/repo.git", ref="dev")
    )
    assert not error, f"Failed to set git registry (status {error.status}): {error.error}"
    # <<<<< SET GIT REGISTRY <<<<<

    # >>>>> OVERWRITE WITH LOCAL >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(type="local", path="/opt/plugins")
    )
    assert not error, f"Failed to set local registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_registry()
    assert not error, f"Failed to get registry (status {error.status}): {error.error}"
    assert response.registry.type == "local"
    assert response.registry.path == "/opt/plugins"
    assert response.registry.url is None, f"Expected no url after overwrite, got: {response.registry.url}"
    # <<<<< OVERWRITE WITH LOCAL <<<<<


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_registry_refresh(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test refreshing the registry index from a remote source.

    1. Refresh without a registry configured, expect error.
    2. Configure the lrr-plugins-demo registry.
    3. Refresh, verify the index is returned with plugins.
    4. Delete registry, verify index is also cleared.
    """
    environment.setup(with_api_key=True)

    # >>>>> REFRESH WITHOUT REGISTRY >>>>>
    response, error = await lrr_client.misc_api.refresh_registry()
    assert error is not None, "Expected error when refreshing without a registry"
    # <<<<< REFRESH WITHOUT REGISTRY <<<<<

    # >>>>> SET REGISTRY AND REFRESH >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to set registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.refresh_registry()
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    assert response.index is not None, "Expected index in refresh response"
    assert response.index.get("version") is not None, "Expected version in index"
    plugins = response.index.get("plugins", {})
    assert len(plugins) > 0, "Expected at least one plugin in index"
    assert "sample-downloader" in plugins, f"Expected sample-downloader in plugins, got: {list(plugins.keys())}"
    # <<<<< SET REGISTRY AND REFRESH <<<<<

    # >>>>> DELETE CLEARS INDEX >>>>>
    response, error = await lrr_client.misc_api.delete_registry()
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.refresh_registry()
    assert error is not None, "Expected error refreshing after registry deleted"
    # <<<<< DELETE CLEARS INDEX <<<<<


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_plugin_install_and_uninstall(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test installing and uninstalling a plugin from the registry.

    1. Configure registry and refresh index.
    2. Install sample-downloader plugin.
    3. Verify plugin appears in plugin list.
    4. Uninstall the plugin.
    5. Verify plugin is no longer listed.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to set registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.refresh_registry()
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL PLUGIN >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader")
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    assert response.namespace == "sample-downloader"
    assert response.name == "Sample Downloader"
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
async def test_plugin_hide_unhide(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test hiding and unhiding a plugin.

    1. Install a plugin from the registry.
    2. Hide the plugin, verify hidden field is true.
    3. Unhide the plugin, verify hidden field is false.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP AND INSTALL >>>>>
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to set registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.refresh_registry()
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
async def test_plugin_install_conflict(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that installing a plugin with a conflicting package name is rejected,
    while non-conflicting installs and upgrades succeed.

    1. Write a .pm file declaring the same namespace as sample-metadata.
    2. Setup environment with the conflicting plugin via plugin_paths.
    3. Configure registry and refresh index.
    4. Install sample-metadata, expect namespace conflict error.
    5. Install sample-downloader (no conflict), expect success.
    6. Reinstall sample-downloader (upgrade), expect success.
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
    response, error = await lrr_client.misc_api.set_registry(
        SetRegistryRequest(
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to set registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.refresh_registry()
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL WITH CONFLICT >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-metadata")
    )
    assert error is not None, "Expected error when installing plugin with package conflict"
    assert "already declared" in error.error, f"Expected 'already declared' in error, got: {error.error}"
    # <<<<< INSTALL WITH CONFLICT <<<<<

    # >>>>> INSTALL WITHOUT CONFLICT >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader")
    )
    assert not error, f"Failed to install non-conflicting plugin (status {error.status}): {error.error}"
    assert response.namespace == "sample-downloader"
    # <<<<< INSTALL WITHOUT CONFLICT <<<<<

    # >>>>> UPGRADE (REINSTALL) >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader")
    )
    assert not error, f"Failed to reinstall/upgrade plugin (status {error.status}): {error.error}"
    # <<<<< UPGRADE (REINSTALL) <<<<<

    expect_no_error_logs(environment, LOGGER)
