"""
Plugin configuration (visibility, priority, execution order) integration tests.
"""

import asyncio
import http
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
    UpdateMetadataPluginConfigRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    install_plugin_and_wait,
    upload_archive,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_save_config_preserves_managed_plugin_provenance(
    lrr_client: LRRClient, environment: AbstractLRRDeploymentContext
):
    """
    Saving plugin configuration must not erase managed-plugin provenance fields.

    1. Disable password protection so POST /config/plugins is reachable without a session.
    2. Create a registry, install sample-metadata (a HASH-param managed plugin).
    3. Capture provenance fields written to LRR_PLUGIN_SAMPLE-METADATA on install.
    4. POST /config/plugins (form-encoded, minimal body) to exercise save_config.
    5. Re-read the same Redis hash and assert installed_path, installed_registry,
       installed_version, installed_sha256, and type survive the save.
    """
    environment.setup(with_api_key=True)
    environment.redis_client.select(2)
    environment.redis_client.hset("LRR_CONFIG", "enablepass", "0")

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    version_key = max(refresh_response.index["plugins"]["sample-metadata"]["versions"].keys())

    response, error = await install_plugin_and_wait(lrr_client,
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id, version=version_key)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"

    redis_key = "LRR_PLUGIN_SAMPLE-METADATA"
    expected = {
        "installed_path": environment.redis_client.hget(redis_key, "installed_path"),
        "installed_registry": environment.redis_client.hget(redis_key, "installed_registry"),
        "installed_version": environment.redis_client.hget(redis_key, "installed_version"),
        "installed_sha256": environment.redis_client.hget(redis_key, "installed_sha256"),
        "type": environment.redis_client.hget(redis_key, "type"),
    }
    for field, value in expected.items():
        assert value, f"Install did not write {field} to {redis_key}; got {value!r}"

    status, body = await lrr_client.handle_request(
        http.HTTPMethod.POST,
        lrr_client.build_url("/config/plugins"),
        headers={},
        data={"replacetitles": "0"},
    )
    assert status == 200, f"POST /config/plugins returned {status}: {body!r}"

    for field, value in expected.items():
        got = environment.redis_client.hget(redis_key, field)
        assert got == value, (
            f"save_config wiped managed plugin {field}: expected {value!r}, got {got!r}"
        )

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("metadata-plugin")
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
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_metadata_version = max(refresh_response.index["plugins"]["sample-metadata"]["versions"].keys())

    response, error = await install_plugin_and_wait(lrr_client,
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id, version=sample_metadata_version)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    # <<<<< SETUP AND INSTALL <<<<<

    # >>>>> DEFAULT FIELD VALUES >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    found_managed = False
    found_default = False
    for plugin in response.plugins:
        if plugin.namespace == "sample-metadata":
            found_managed = True
            assert plugin.hidden is False, f"Fresh install expected hidden=False, got {plugin.hidden}"
            assert plugin.registry == reg_id, (
                f"Managed plugin expected registry={reg_id}, got {plugin.registry}"
            )
        if plugin.namespace == "copytags":
            found_default = True
            assert plugin.registry is None, (
                f"Default plugin expected registry=None, got {plugin.registry}"
            )
    assert found_managed, "sample-metadata not found in plugin list after install"
    assert found_default, "default plugin copytags not found in plugin list"
    # <<<<< DEFAULT FIELD VALUES <<<<<

    # >>>>> HIDE PLUGIN >>>>>
    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "sample-metadata", UpdateMetadataPluginConfigRequest(hidden=True)
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
    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "sample-metadata", UpdateMetadataPluginConfigRequest(hidden=False)
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
    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "sample-metadata", UpdateMetadataPluginConfigRequest(hidden=True, priority=7)
    )
    assert not error, f"Failed to set hidden+priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.uninstall_plugin("sample-metadata")
    assert not error, f"Failed to uninstall plugin (status {error.status}): {error.error}"

    response, error = await install_plugin_and_wait(lrr_client,
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id, version=sample_metadata_version)
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
    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "copytags", UpdateMetadataPluginConfigRequest(hidden=True)
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

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "copytags", UpdateMetadataPluginConfigRequest(hidden=False)
    )
    assert not error, f"Failed to unhide built-in plugin (status {error.status}): {error.error}"
    # <<<<< HIDE BUILT-IN PLUGIN <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("metadata-plugin")
async def test_plugin_config_nonexistent_namespace(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """Updating config for a never-installed namespace returns 404."""
    environment.setup(with_api_key=True)

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "definitely-not-real", UpdateMetadataPluginConfigRequest(hidden=True)
    )
    assert error is not None, "Expected 404 error for nonexistent namespace"
    assert error.status == 404, f"Expected 404 for nonexistent namespace, got {error.status}"

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("metadata-plugin")
@pytest.mark.ratelimit
async def test_plugin_priority(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test plugin priority via update_metadata_plugin_config.

    1. Create registry, refresh, install sample-metadata.
    2. Verify default priority is 0.
    3. Set priority to 5, verify it persists in plugin list.
    4. Set distinct priorities on sample-metadata and a default metadata plugin, verify both.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP AND INSTALL >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_metadata_version = max(refresh_response.index["plugins"]["sample-metadata"]["versions"].keys())

    response, error = await install_plugin_and_wait(lrr_client,
        InstallPluginRequest(namespace="sample-metadata", registry=reg_id, version=sample_metadata_version)
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
    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "sample-metadata", UpdateMetadataPluginConfigRequest(priority=5)
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
    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "copytags", UpdateMetadataPluginConfigRequest(priority=3)
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

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("metadata-plugin")
@pytest.mark.ratelimit
async def test_plugin_config_rejects_non_metadata_fields(
    lrr_client: LRRClient, environment: AbstractLRRDeploymentContext
):
    """
    Test that update_metadata_plugin_config rejects metadata-only fields on non-metadata plugins.

    Per spec: enabled, priority, and hidden are properties of metadata plugins.
    Non-metadata plugins (login, download, script) cannot carry these fields.

    1. Create registry, refresh, install sample-downloader (a download plugin).
    2. Attempt to set enabled=True; expect 400.
    3. Attempt to set priority=2; expect 400.
    4. Attempt to set hidden=True; expect 400.
    """
    environment.setup(with_api_key=True)

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_downloader_version = max(refresh_response.index["plugins"]["sample-downloader"]["versions"].keys())

    response, error = await install_plugin_and_wait(lrr_client,
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=sample_downloader_version)
    )
    assert not error, f"Failed to install sample-downloader (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "sample-downloader", UpdateMetadataPluginConfigRequest(enabled=True)
    )
    assert error and error.status == 400, (
        f"Expected 400 rejecting enabled on download plugin, got status={error.status if error else 'no error'}"
    )

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "sample-downloader", UpdateMetadataPluginConfigRequest(priority=2)
    )
    assert error and error.status == 400, (
        f"Expected 400 rejecting priority on download plugin, got status={error.status if error else 'no error'}"
    )

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "sample-downloader", UpdateMetadataPluginConfigRequest(hidden=True)
    )
    assert error and error.status == 400, (
        f"Expected 400 rejecting hidden on download plugin, got status={error.status if error else 'no error'}"
    )

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("metadata-plugin")
async def test_plugin_config_returns_500_on_corrupt_type(
    lrr_client: LRRClient, environment: AbstractLRRDeploymentContext
):
    """
    Test that update_metadata_plugin_config returns 500 when a plugin's type is missing from Redis.

    Simulates a corrupt registration state by deleting the `type` field from a built-in metadata
    plugin's Redis hash, then calling the config endpoint. The handler logs an error and returns 500.

    1. Set up environment with a known built-in metadata plugin (copytags).
    2. Delete the `type` field from its Redis hash.
    3. Attempt to set hidden=True; expect 500.
    """
    environment.setup(with_api_key=True)

    environment.redis_client.hdel("LRR_PLUGIN_COPYTAGS", "type")

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "copytags", UpdateMetadataPluginConfigRequest(hidden=True)
    )
    assert error and error.status == 500, (
        f"Expected 500 on corrupt type, got status={error.status if error else 'no error'}"
    )


@pytest.mark.asyncio
@pytest.mark.dev("metadata-plugin")
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
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL ALL THREE >>>>>
    for ns in ("title-suffix-1", "title-suffix-2", "title-suffix-3"):
        version_key = max(refresh_response.index["plugins"][ns]["versions"].keys())
        response, error = await install_plugin_and_wait(lrr_client,
            InstallPluginRequest(namespace=ns, registry=reg_id, version=version_key)
        )
        assert not error, f"Failed to install {ns} (status {error.status}): {error.error}"
    # <<<<< INSTALL ALL THREE <<<<<

    # >>>>> SET PRIORITIES: 2, 1, 3 >>>>>
    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "title-suffix-2", UpdateMetadataPluginConfigRequest(priority=1)
    )
    assert not error, f"Failed to set suffix-2 priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "title-suffix-1", UpdateMetadataPluginConfigRequest(priority=2)
    )
    assert not error, f"Failed to set suffix-1 priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "title-suffix-3", UpdateMetadataPluginConfigRequest(priority=3)
    )
    assert not error, f"Failed to set suffix-3 priority (status {error.status}): {error.error}"
    # <<<<< SET PRIORITIES <<<<<

    # >>>>> ENABLE ALL THREE >>>>>
    for ns in ("title-suffix-1", "title-suffix-2", "title-suffix-3"):
        response, error = await lrr_client.misc_api.update_metadata_plugin_config(
            ns, UpdateMetadataPluginConfigRequest(enabled=True)
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
    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "title-suffix-3", UpdateMetadataPluginConfigRequest(priority=1)
    )
    assert not error, f"Failed to set suffix-3 priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "title-suffix-2", UpdateMetadataPluginConfigRequest(priority=2)
    )
    assert not error, f"Failed to set suffix-2 priority (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.update_metadata_plugin_config(
        "title-suffix-1", UpdateMetadataPluginConfigRequest(priority=3)
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
