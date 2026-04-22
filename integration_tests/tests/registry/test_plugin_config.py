"""
Plugin configuration (visibility, priority, execution order) integration tests.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import GetArchiveMetadataRequest
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
    InstallPluginRequest,
    UpdatePluginConfigRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive

LOGGER = logging.getLogger(__name__)


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
async def test_plugin_config_nonexistent_namespace(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """Updating config for a never-installed namespace returns 404."""
    environment.setup(with_api_key=True)

    response, error = await lrr_client.misc_api.update_plugin_config(
        "definitely-not-real", UpdatePluginConfigRequest(hidden=True)
    )
    assert error is not None, "Expected 404 error for nonexistent namespace"
    assert error.status == 404, f"Expected 404 for nonexistent namespace, got {error.status}"


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
