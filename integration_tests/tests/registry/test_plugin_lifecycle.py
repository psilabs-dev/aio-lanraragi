"""
Plugin install/uninstall lifecycle integration tests.
"""

import asyncio
import hashlib
import json
import logging
import tempfile
import time
from pathlib import Path

import aiohttp
import playwright.async_api
import playwright.async_api._generated
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import GetArchiveMetadataRequest
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
    InstallPluginRequest,
    UpdatePluginConfigRequest,
    UpdateRegistryRequest,
    UsePluginRequest,
)

from aio_lanraragi_tests.common import DEFAULT_LRR_PASSWORD
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    sideload_plugin,
    upload_archive,
)
from aio_lanraragi_tests.utils.playwright import (
    assert_browser_responses_ok,
    assert_console_logs_ok,
)

LOGGER = logging.getLogger(__name__)

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
    refresh_response = response
    version_key = refresh_response.index["plugins"]["sample-downloader"]["channels"]["latest"]
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=version_key)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    assert response.namespace == "sample-downloader"
    assert response.name == "Sample Downloader"
    assert response.installed_registry == reg_id, f"Expected provenance {reg_id}, got: {response.installed_registry}"
    # <<<<< INSTALL PLUGIN <<<<<

    # >>>>> VERIFY INSTALLED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    sample = next((p for p in response.plugins if p.namespace == "sample-downloader"), None)
    assert sample is not None, "sample-downloader missing from download plugin list after install"
    assert sample.installed_registry == reg_id, (
        f"Expected managed provenance {reg_id}, got: {sample.installed_registry}"
    )
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
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    metadata_before = {p.namespace for p in response.plugins}
    assert "copytags" in metadata_before, f"copytags missing from metadata plugin list before uninstall attempt: {metadata_before}"

    response, error = await lrr_client.misc_api.uninstall_plugin("copytags")
    assert error is not None, "Expected error uninstalling built-in plugin"
    assert error.status == 403, f"Expected 403 for built-in uninstall, got {error.status}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    metadata_after = {p.namespace for p in response.plugins}
    assert metadata_after == metadata_before, (
        f"Metadata plugin list changed after blocked uninstall. "
        f"Removed: {metadata_before - metadata_after}, added: {metadata_after - metadata_before}"
    )
    # <<<<< UNINSTALL BUILT-IN BLOCKED <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_install_provenance_fields(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test provenance fields for channel-tracked and explicit-version installs.

    1. Install sample-downloader with installed_channel="latest".
    2. Assert install response and plugin list include required sha256/channel provenance.
    3. Reinstall explicitly after uninstalling.
    4. Assert installed_channel is cleared while sha256 remains.
    """
    environment.setup(with_api_key=True)

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

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

    version_key = refresh_response.index["plugins"]["sample-downloader"]["channels"]["latest"]
    version_record = refresh_response.index["plugins"]["sample-downloader"]["versions"][version_key]
    expected_sha = version_record["sha256"]

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(
            namespace="sample-downloader",
            registry=reg_id,
            version=version_key,
            installed_channel="latest",
        )
    )
    assert not error, f"Failed to install channel-tracked plugin (status {error.status}): {error.error}"
    assert response.installed_registry == reg_id, f"Expected provenance {reg_id}, got: {response.installed_registry}"
    assert response.installed_sha256 == expected_sha, (
        f"Expected install sha256 {expected_sha}, got {response.installed_sha256}"
    )
    assert response.installed_channel == "latest", (
        f"Expected install channel 'latest', got {response.installed_channel!r}"
    )
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    plugin = next((p for p in response.plugins if p.namespace == "sample-downloader"), None)
    assert plugin is not None, "sample-downloader missing from plugin list after install"
    assert plugin.installed_registry == reg_id, f"Expected managed provenance {reg_id}, got: {plugin.installed_registry}"
    assert plugin.installed_version == version_key, (
        f"Expected installed_version {version_key!r}, got {plugin.installed_version!r}"
    )
    assert plugin.installed_sha256 == expected_sha, (
        f"Expected installed_sha256 {expected_sha}, got {plugin.installed_sha256!r}"
    )
    assert plugin.installed_channel == "latest", (
        f"Expected installed_channel 'latest', got {plugin.installed_channel!r}"
    )
    environment.restart()

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins after restart (status {error.status}): {error.error}"
    plugin = next((p for p in response.plugins if p.namespace == "sample-downloader"), None)
    assert plugin is not None, "sample-downloader missing from plugin list after restart"
    assert plugin.installed_channel == "latest", (
        f"Expected installed_channel 'latest' after restart, got {plugin.installed_channel!r}"
    )
    assert plugin.installed_sha256 == expected_sha, (
        f"Expected installed_sha256 {expected_sha} after restart, got {plugin.installed_sha256!r}"
    )
    response, error = await lrr_client.misc_api.uninstall_plugin("sample-downloader")
    assert not error, f"Failed to uninstall plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=version_key)
    )
    assert not error, f"Failed to install explicit-version plugin (status {error.status}): {error.error}"
    assert response.installed_channel is None, (
        f"Expected no installed_channel for explicit install, got {response.installed_channel!r}"
    )
    assert response.installed_sha256 == expected_sha, (
        f"Expected install sha256 {expected_sha}, got {response.installed_sha256}"
    )
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins after explicit install (status {error.status}): {error.error}"
    plugin = next((p for p in response.plugins if p.namespace == "sample-downloader"), None)
    assert plugin is not None, "sample-downloader missing after explicit reinstall"
    assert plugin.installed_channel is None, (
        f"Expected installed_channel to clear after explicit install, got {plugin.installed_channel!r}"
    )
    assert plugin.installed_sha256 == expected_sha, (
        f"Expected installed_sha256 {expected_sha}, got {plugin.installed_sha256!r}"
    )

    # >>>>> SIDELOAD COUNTERPART PROVENANCE >>>>>
    # Sideload a download plugin under a sister namespace and assert that only
    # installed_path is written to Redis; the four managed provenance fields
    # (installed_registry/version/sha256/channel) must be absent from the hash.
    # The assertion reads Redis directly because a freshly sideloaded plugin
    # may not appear in get_available_plugins until plugin discovery refreshes,
    # and the parity claim is about Redis state, not list visibility.
    sideload_ns = "test-sideload-provenance-1"
    sideload_pm_name = "TestSideloadProvenance1.pm"
    sideload_pm_body = (
        "package LANraragi::Plugin::Download::TestSideloadProvenance1;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        "sub plugin_info {\n"
        "    return (\n"
        "        name      => 'Test Sideload Provenance 1',\n"
        "        type      => 'download',\n"
        f"        namespace => '{sideload_ns}',\n"
        "        author    => 'test',\n"
        "        version   => '1.0',\n"
        "    );\n"
        "}\n"
        "sub provide_url { return; }\n"
        "1;\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        pm_path = Path(tmpdir) / sideload_pm_name
        pm_path.write_text(sideload_pm_body, encoding="utf-8")
        status, content = await sideload_plugin(lrr_client, pm_path, DEFAULT_LRR_PASSWORD)
    assert status == 200, f"Expected 200 for sideload, got {status}: {content}"
    assert '"success":1' in content, f"Expected sideload to succeed, got: {content}"

    environment.redis_client.select(2)
    sideload_redis_key = f"LRR_PLUGIN_{sideload_ns.upper()}"
    sideload_hash = environment.redis_client.hgetall(sideload_redis_key)
    assert sideload_hash.get("installed_path") == f"LANraragi/Plugin/Sideloaded/{sideload_pm_name}", (
        f"Expected installed_path LANraragi/Plugin/Sideloaded/{sideload_pm_name!r} in Redis, "
        f"got hash: {sideload_hash}"
    )
    for provenance_field in ("installed_registry", "installed_version", "installed_sha256", "installed_channel"):
        assert provenance_field not in sideload_hash, (
            f"Expected {provenance_field} absent from sideload Redis hash, got: {sideload_hash}"
        )

    _, error = await lrr_client.misc_api.uninstall_plugin(sideload_ns)
    assert not error, f"Failed to uninstall sideloaded plugin (status {error.status}): {error.error}"
    # <<<<< SIDELOAD COUNTERPART PROVENANCE <<<<<

    _, error = await lrr_client.misc_api.uninstall_plugin("sample-downloader")
    assert not error, f"Failed to uninstall sample-downloader (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

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
        InstallPluginRequest(namespace="sample-downloader", registry="REG_0000000001", version="1.0")
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
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version="1.0")
    )
    assert error is not None, "Expected error when installing without refresh"
    assert error.status == 409, f"Expected 409 for no cached index, got {error.status}"
    # <<<<< INSTALL WITHOUT REFRESH <<<<<

    # >>>>> INSTALL NONEXISTENT NAMESPACE >>>>>
    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="does-not-exist-xyz", registry=reg_id, version="1.0")
    )
    assert error is not None, "Expected error for nonexistent namespace"
    assert error.status == 404, f"Expected 404 for unknown namespace, got {error.status}"
    # <<<<< INSTALL NONEXISTENT NAMESPACE <<<<<

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry-tx") # transactional (tx)
async def test_plugin_install_failed_require_rolls_back(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that a managed install rolls back when the plugin file fails to require.

    1. Create local registry with one broken plugin (valid Perl, BEGIN { die }).
    2. Refresh registry.
    3. Install the broken plugin; expect a non-2xx error response.
    4. Assert plugin file is absent on the host.
    5. Assert Redis hash for the namespace is empty.
    6. Assert namespace is absent from GET /api/plugins/metadata.
    """
    environment.setup(with_api_key=True)

    broken_ns = "sample-broken-tx-1"
    broken_pm_name = "SampleBrokenTx1.pm"
    broken_pm_body = (
        "package LANraragi::Plugin::Managed::Metadata::SampleBrokenTx1;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        "BEGIN { die 'boom' }\n"
        "sub plugin_info {\n"
        "    return (\n"
        "        name      => 'sample-broken-tx-1',\n"
        "        type      => 'metadata',\n"
        f"        namespace => '{broken_ns}',\n"
        "        author    => 'test',\n"
        "        version   => '1.0',\n"
        "    );\n"
        "}\n"
        "sub get_tags { return (); }\n"
        "1;\n"
    )
    broken_pm_bytes = broken_pm_body.encode("utf-8")
    broken_sha = hashlib.sha256(broken_pm_bytes).hexdigest()
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    plugin_rel_path = f"artifacts/{broken_ns}/1.0/{broken_pm_name}"

    registry_data = {
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            broken_ns: {
                "namespace": broken_ns,
                "type": "metadata",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "sample-broken-tx-1",
                        "author": "test",
                        "description": "broken require test plugin",
                        "artifact": plugin_rel_path,
                        "sha256": broken_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }

    plugin_file = environment.local_registry_dir / plugin_rel_path
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_bytes(broken_pm_bytes)

    registry_json = environment.local_registry_dir / "registry.json"
    registry_json.write_text(json.dumps(registry_data), encoding="utf-8")

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="local-broken",
            type="local",
            path=environment.local_registry_path,
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL BROKEN PLUGIN >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace=broken_ns, registry=reg_id, version="1.0")
    )
    assert error is not None, "Expected error for broken plugin install"
    assert error.status >= 400, f"Expected non-2xx status for broken plugin install, got {error.status}"
    LOGGER.debug(f"Install broken plugin: status={error.status}, error={error.error!r}")
    # <<<<< INSTALL BROKEN PLUGIN <<<<<

    # >>>>> ROLLBACK ASSERTIONS >>>>>
    target_pm = environment.plugin_managed_dir / "Metadata" / broken_pm_name
    assert not target_pm.exists(), f"Plugin file should be absent after failed install: {target_pm}"

    environment.redis_client.select(2)
    redis_key = f"LRR_PLUGIN_{broken_ns.upper()}"
    redis_hash = environment.redis_client.hgetall(redis_key)
    assert not redis_hash, f"Expected empty Redis hash for {broken_ns} after failed install, got: {redis_hash}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list metadata plugins (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert broken_ns not in namespaces, f"{broken_ns} must be absent after failed install, got: {namespaces}"
    # <<<<< ROLLBACK ASSERTIONS <<<<<

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    # expect_no_error_logs is intentionally omitted: the install attempt against
    # a deliberately broken plugin causes LRR to log a server-side error
    # describing the failed require/rollback. That log is expected, not a defect.


@pytest.mark.asyncio
@pytest.mark.dev("registry-tx") # transactional (tx)
async def test_install_failure_preserves_other_plugins(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that a failed managed install does not disturb a previously installed plugin.

    1. Create local registry with two plugins: sample-good (loadable metadata) and sample-broken (BEGIN die).
    2. Install sample-good; assert success and capture full state.
    3. Install sample-broken; expect a non-2xx error response.
    4. Assert rollback for sample-broken (file absent, Redis empty, not in plugin list).
    5. Re-fetch sample-good state; assert it matches the captured snapshot byte-for-byte.
    """
    environment.setup(with_api_key=True)

    good_ns = "sample-good-tx-1"
    good_pm_name = "SampleGoodTx1.pm"
    good_pm_body = (
        "package LANraragi::Plugin::Managed::Metadata::SampleGoodTx1;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        "sub plugin_info {\n"
        "    return (\n"
        "        name      => 'sample-good-tx-1',\n"
        "        type      => 'metadata',\n"
        f"        namespace => '{good_ns}',\n"
        "        author    => 'test',\n"
        "        version   => '1.0',\n"
        "    );\n"
        "}\n"
        "sub get_tags { return (); }\n"
        "1;\n"
    )

    broken_ns = "sample-broken-tx-2"
    broken_pm_name = "SampleBrokenTx2.pm"
    broken_pm_body = (
        "package LANraragi::Plugin::Managed::Metadata::SampleBrokenTx2;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        "BEGIN { die 'boom' }\n"
        "sub plugin_info {\n"
        "    return (\n"
        "        name      => 'sample-broken-tx-2',\n"
        "        type      => 'metadata',\n"
        f"        namespace => '{broken_ns}',\n"
        "        author    => 'test',\n"
        "        version   => '1.0',\n"
        "    );\n"
        "}\n"
        "sub get_tags { return (); }\n"
        "1;\n"
    )

    good_pm_bytes = good_pm_body.encode("utf-8")
    broken_pm_bytes = broken_pm_body.encode("utf-8")
    good_sha = hashlib.sha256(good_pm_bytes).hexdigest()
    broken_sha = hashlib.sha256(broken_pm_bytes).hexdigest()
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    good_rel_path = f"artifacts/{good_ns}/1.0/{good_pm_name}"
    broken_rel_path = f"artifacts/{broken_ns}/1.0/{broken_pm_name}"

    good_file = environment.local_registry_dir / good_rel_path
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_bytes(good_pm_bytes)

    broken_file = environment.local_registry_dir / broken_rel_path
    broken_file.parent.mkdir(parents=True, exist_ok=True)
    broken_file.write_bytes(broken_pm_bytes)

    registry_data = {
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            good_ns: {
                "namespace": good_ns,
                "type": "metadata",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "sample-good-tx-1",
                        "author": "test",
                        "description": "good metadata test plugin",
                        "artifact": good_rel_path,
                        "sha256": good_sha,
                        "published_at": generated_at,
                    },
                },
            },
            broken_ns: {
                "namespace": broken_ns,
                "type": "metadata",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "sample-broken-tx-2",
                        "author": "test",
                        "description": "broken metadata test plugin",
                        "artifact": broken_rel_path,
                        "sha256": broken_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }
    registry_json = environment.local_registry_dir / "registry.json"
    registry_json.write_text(json.dumps(registry_data), encoding="utf-8")

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="local-two-plugins",
            type="local",
            path=environment.local_registry_path,
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL GOOD PLUGIN AND CAPTURE STATE >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace=good_ns, registry=reg_id, version="1.0")
    )
    assert not error, f"Failed to install good plugin (status {error.status}): {error.error}"
    assert response.namespace == good_ns

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list metadata plugins (status {error.status}): {error.error}"
    good_plugin_before = next((p for p in response.plugins if p.namespace == good_ns), None)
    assert good_plugin_before is not None, f"{good_ns} missing from plugin list after install"

    environment.redis_client.select(2)
    good_redis_key = f"LRR_PLUGIN_{good_ns.upper()}"
    good_redis_before = environment.redis_client.hgetall(good_redis_key)
    assert good_redis_before, f"Expected non-empty Redis hash for {good_ns} after install"
    LOGGER.debug(f"Captured good plugin Redis state: {good_redis_before}")
    # <<<<< INSTALL GOOD PLUGIN AND CAPTURE STATE <<<<<

    # >>>>> INSTALL BROKEN PLUGIN >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace=broken_ns, registry=reg_id, version="1.0")
    )
    assert error is not None, "Expected error for broken plugin install"
    assert error.status >= 400, f"Expected non-2xx status for broken plugin install, got {error.status}"
    LOGGER.debug(f"Install broken plugin: status={error.status}, error={error.error!r}")
    # <<<<< INSTALL BROKEN PLUGIN <<<<<

    # >>>>> ROLLBACK ASSERTIONS FOR BROKEN >>>>>
    broken_target = environment.plugin_managed_dir / "Metadata" / broken_pm_name
    assert not broken_target.exists(), f"Broken plugin file should be absent after failed install: {broken_target}"

    environment.redis_client.select(2)
    broken_redis_key = f"LRR_PLUGIN_{broken_ns.upper()}"
    broken_redis_hash = environment.redis_client.hgetall(broken_redis_key)
    assert not broken_redis_hash, (
        f"Expected empty Redis hash for {broken_ns} after failed install, got: {broken_redis_hash}"
    )

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list metadata plugins (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert broken_ns not in namespaces, f"{broken_ns} must be absent after failed install, got: {namespaces}"
    # <<<<< ROLLBACK ASSERTIONS FOR BROKEN <<<<<

    # >>>>> GOOD PLUGIN STATE UNCHANGED >>>>>
    good_plugin_after = next((p for p in response.plugins if p.namespace == good_ns), None)
    assert good_plugin_after is not None, f"{good_ns} must still be listed after broken install attempt"
    assert good_plugin_after == good_plugin_before, (
        f"Good plugin API state changed after broken install attempt.\n"
        f"Before: {good_plugin_before}\nAfter: {good_plugin_after}"
    )

    environment.redis_client.select(2)
    good_redis_after = environment.redis_client.hgetall(good_redis_key)
    assert good_redis_after == good_redis_before, (
        f"Good plugin Redis hash changed after broken install attempt.\n"
        f"Before: {good_redis_before}\nAfter: {good_redis_after}"
    )
    # <<<<< GOOD PLUGIN STATE UNCHANGED <<<<<

    response, error = await lrr_client.misc_api.uninstall_plugin(good_ns)
    assert not error, f"Failed to uninstall good plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    # expect_no_error_logs is intentionally omitted: the broken-plugin install
    # attempt causes LRR to log a server-side error describing the failed
    # require/rollback. That log is expected, not a defect.


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

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    title_suffix_1_version = refresh_response.index["plugins"]["title-suffix-1"]["channels"]["latest"]
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="title-suffix-1", registry=reg_id, version=title_suffix_1_version)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    assert response.installed_registry == reg_id, f"Expected provenance {reg_id}, got: {response.installed_registry}"
    # <<<<< INSTALL <<<<<

    # >>>>> VERIFY INSTALLED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "title-suffix-1":
            assert plugin.installed_registry == reg_id, f"Expected managed provenance {reg_id}, got: {plugin.installed_registry}"
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
        InstallPluginRequest(namespace="title-suffix-1", registry=reg_id, version=title_suffix_1_version)
    )
    assert not error, f"Failed to reinstall plugin (status {error.status}): {error.error}"
    assert response.installed_registry == reg_id, f"Expected provenance on reinstall {reg_id}, got: {response.installed_registry}"
    # <<<<< REINSTALL <<<<<

    # >>>>> VERIFY REINSTALLED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after reinstall (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "title-suffix-1":
            assert plugin.installed_registry == reg_id, f"Expected managed provenance {reg_id}, got: {plugin.installed_registry}"
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
            assert plugin.installed_registry == reg_id, f"Expected orphaned provenance {reg_id}, got: {plugin.installed_registry}"
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
async def test_plugin_install_conflict(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test plugin install conflict detection and force install.

    1. Write a .pm file declaring the same namespace as sample-metadata.
    2. Setup environment with the conflicting plugin.
    3. Create registry and refresh index.
    4. Install sample-metadata, expect non-managed conflict (400) -- user must remove first.
    5. Force install sample-metadata, expect same non-managed conflict (400) -- force does not bypass.
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

        refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
        assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
        sample_metadata_version = refresh_response.index["plugins"]["sample-metadata"]["channels"]["latest"]
        sample_downloader_version = refresh_response.index["plugins"]["sample-downloader"]["channels"]["latest"]
        # <<<<< SETUP REGISTRY <<<<<

        # >>>>> INSTALL WITH NON-MANAGED CONFLICT >>>>>
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace="sample-metadata", registry=reg_id, version=sample_metadata_version)
        )
        assert error is not None, "Expected error when installing plugin with existing non-managed copy"
        assert error.status == 400, f"Expected 400 for non-managed conflict, got {error.status}"
        assert "Remove it first" in error.error, f"Expected 'Remove it first' in error, got: {error.error}"
        # <<<<< INSTALL WITH NON-MANAGED CONFLICT <<<<<

        # >>>>> FORCE INSTALL STILL BLOCKED OVER NON-MANAGED >>>>>
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace="sample-metadata", registry=reg_id, version=sample_metadata_version, force=True)
        )
        assert error is not None, "Expected error: force must not bypass non-managed conflict"
        assert error.status == 400, f"Expected 400 for non-managed conflict (force), got {error.status}"
        assert "Remove it first" in error.error, f"Expected 'Remove it first' in error, got: {error.error}"
        # <<<<< FORCE INSTALL STILL BLOCKED OVER NON-MANAGED <<<<<

        # >>>>> INSTALL WITHOUT CONFLICT >>>>>
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=sample_downloader_version)
        )
        assert not error, f"Failed to install non-conflicting plugin (status {error.status}): {error.error}"
        assert response.namespace == "sample-downloader"
        assert response.installed_registry == reg_id, f"Expected provenance {reg_id}, got: {response.installed_registry}"
        # <<<<< INSTALL WITHOUT CONFLICT <<<<<

        # >>>>> UPGRADE (REINSTALL) >>>>>
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=sample_downloader_version)
        )
        assert not error, f"Failed to reinstall/upgrade plugin (status {error.status}): {error.error}"
        # <<<<< UPGRADE (REINSTALL) <<<<<

        expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_uninstall_not_listed(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that uninstalled plugin is absent from plugin list across repeated cycles.

    Worker-lottery regression check: under prefork, each request may land on a
    different worker, so a single uninstall->list cycle does not exercise every
    worker's module/cache state. 5 cycles raise the probability that every
    worker observes both the install and the post-uninstall state.

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

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_login_version = refresh_response.index["plugins"]["sample-login"]["channels"]["latest"]
    # <<<<< SETUP REGISTRY <<<<<

    for i in range(5):
        LOGGER.debug(f"Cycle {i}: installing sample-login")
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace="sample-login", registry=reg_id, version=sample_login_version)
        )
        assert not error, f"Cycle {i}: install failed (status {error.status}): {error.error}"

        LOGGER.debug(f"Cycle {i}: uninstalling sample-login")
        response, error = await lrr_client.misc_api.uninstall_plugin("sample-login")
        assert not error, f"Cycle {i}: uninstall failed (status {error.status}): {error.error}"

        LOGGER.debug(f"Cycle {i}: verifying absent from plugin list")
        response, error = await lrr_client.misc_api.get_available_plugins(
            GetAvailablePluginsRequest(type="login")
        )
        assert not error, f"Cycle {i}: list failed (status {error.status}): {error.error}"
        namespaces = {p.namespace for p in response.plugins}
        assert "sample-login" not in namespaces, f"Cycle {i}: sample-login still listed after uninstall: {namespaces}"

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_cross_provenance_force(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test cross-provenance force install flow: orphan upgrade attempt, mismatch error, and forced re-attribution.

    1. Create reg A, refresh, install sample-downloader -> 200.
    2. Delete reg A -> plugin becomes orphan (registry field still points to A_id).
    3. Install from A_id -> 404 (registry not found).
    4. Create reg B (same URL/ref), different timestamp id.
    5. Install from B without force -> provenance mismatch error.
    6. Install from B with force=True -> 200, provenance updated to B_id.
    7. GET download plugins -> sample-downloader present with registry == B_id.
    """
    environment.setup(with_api_key=True)

    # >>>>> SETUP REG A >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo-A",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create reg A (status {error.status}): {error.error}"
    reg_a_id = response.id

    refresh_a_response, error = await lrr_client.misc_api.refresh_registry(reg_a_id)
    assert not error, f"Failed to refresh reg A (status {error.status}): {error.error}"
    sample_downloader_version = refresh_a_response.index["plugins"]["sample-downloader"]["channels"]["latest"]
    # <<<<< SETUP REG A <<<<<

    # >>>>> INSTALL FROM REG A >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_a_id, version=sample_downloader_version)
    )
    assert not error, f"Failed to install sample-downloader from reg A (status {error.status}): {error.error}"
    assert response.installed_registry == reg_a_id, f"Expected provenance {reg_a_id}, got: {response.installed_registry}"
    # <<<<< INSTALL FROM REG A <<<<<

    # >>>>> DELETE REG A -> ORPHAN >>>>>
    response, error = await lrr_client.misc_api.delete_registry(reg_a_id)
    assert not error, f"Failed to delete reg A (status {error.status}): {error.error}"
    # Registry IDs are REG_{unix_timestamp}. Guarantee reg B gets a distinct
    # timestamp so the provenance mismatch scenario below is actually reached.
    await asyncio.sleep(1.0)
    # <<<<< DELETE REG A -> ORPHAN <<<<<

    # >>>>> UPGRADE WITH ORPHAN REGISTRY -> 404 >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_a_id, version=sample_downloader_version)
    )
    assert error is not None, "Expected error when installing from deleted registry"
    assert error.status == 404, f"Expected 404 for deleted registry, got {error.status}"
    # <<<<< UPGRADE WITH ORPHAN REGISTRY -> 404 <<<<<

    # >>>>> CREATE REG B (SAME SOURCE) >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo-B",
            type="git",
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create reg B (status {error.status}): {error.error}"
    reg_b_id = response.id
    assert reg_b_id != reg_a_id, "Expected reg B to have a different id than reg A"

    refresh_b_response, error = await lrr_client.misc_api.refresh_registry(reg_b_id)
    assert not error, f"Failed to refresh reg B (status {error.status}): {error.error}"
    sample_downloader_version_b = refresh_b_response.index["plugins"]["sample-downloader"]["channels"]["latest"]
    # <<<<< CREATE REG B (SAME SOURCE) <<<<<

    # >>>>> INSTALL FROM REG B WITHOUT FORCE -> PROVENANCE MISMATCH >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_b_id, version=sample_downloader_version_b)
    )
    assert error is not None, "Expected provenance mismatch error when installing from different registry without force"
    assert error.status == 400, f"Expected 400 for cross-registry provenance mismatch, got {error.status}"
    # <<<<< INSTALL FROM REG B WITHOUT FORCE -> PROVENANCE MISMATCH <<<<<

    # >>>>> INSTALL FROM REG B WITH FORCE -> 200 >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_b_id, version=sample_downloader_version_b, force=True)
    )
    assert not error, f"Expected force install to succeed (status {error.status}): {error.error}"
    assert response.installed_registry == reg_b_id, f"Expected provenance {reg_b_id} after force install, got: {response.installed_registry}"
    # <<<<< INSTALL FROM REG B WITH FORCE -> 200 <<<<<

    # >>>>> VERIFY PROVENANCE UPDATED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list download plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-downloader":
            assert plugin.installed_registry == reg_b_id, f"Expected provenance {reg_b_id}, got: {plugin.installed_registry}"
            break
    else:
        pytest.fail("sample-downloader not found in download plugin list after force install")
    # <<<<< VERIFY PROVENANCE UPDATED <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_sideload_after_managed_uninstall_no_duplicate_rows(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Reproduce: install managed -> uninstall -> sideload -> server renders 2 rows.

    Single worker so all operations hit the same Perl process. No restart
    between the cycle and the check — the bug is per-worker symbol table
    state that a restart would clear.

    1. Install managed sample-script from registry.
    2. Uninstall the managed sample-script (file deleted, class stays in symbol table).
    3. Sideload SampleScript.pm (new class loaded in same worker).
    4. Fetch /config/plugins raw HTML and count sample-script rows.
    """
    plugin_path = Path(__file__).parent.parent / "resources" / "plugins" / "scripts" / "SampleScript.pm"
    assert plugin_path.exists(), f"Test plugin file not found: {plugin_path}"

    environment.setup(with_api_key=True)

    # >>>>> INSTALL MANAGED >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="demo", type="git", provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git", ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_script_version = refresh_response.index["plugins"]["sample-script"]["channels"]["latest"]

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=sample_script_version)
    )
    assert not error, f"Failed to install managed sample-script (status {error.status}): {error.error}"
    # <<<<< INSTALL MANAGED <<<<<

    # >>>>> UNINSTALL MANAGED >>>>>
    _, error = await lrr_client.misc_api.uninstall_plugin("sample-script")
    assert not error, f"Failed to uninstall managed sample-script (status {error.status}): {error.error}"
    # <<<<< UNINSTALL MANAGED <<<<<

    # >>>>> SIDELOAD >>>>>
    status, content = await sideload_plugin(lrr_client, plugin_path, DEFAULT_LRR_PASSWORD)
    assert status == 200, f"Expected 200 upload status, got {status}: {content}"
    assert '"success":1' in content, f"Expected sideload to succeed, got: {content}"
    # <<<<< SIDELOAD <<<<<

    # >>>>> RAW HTML CHECK — NO RESTART, SAME WORKER >>>>>
    login_url = lrr_client.misc_api.api_context.build_url("/login")
    plugins_url = lrr_client.misc_api.api_context.build_url("/config/plugins")

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        login_form = aiohttp.FormData(quote_fields=False)
        login_form.add_field("password", DEFAULT_LRR_PASSWORD)
        login_form.add_field("redirect", "index")
        async with session.post(login_url, data=login_form) as resp:
            assert resp.status == 200

        # Fetch raw HTML 20 times across default workers (typically 4).
        # The affected worker returns 2 rows; others return 1. With 4 workers
        # and 20 fetches, P(never hitting the affected worker) < 0.3%.
        for i in range(20):
            async with session.get(plugins_url) as resp:
                html = await resp.text()
                matches = html.count('data-namespace="sample-script" data-source=')
                LOGGER.debug(f"Fetch {i}: {matches} sample-script row(s) in server HTML")
                assert matches <= 1, \
                    f"Fetch {i}: expected at most 1 sample-script row in server HTML, got {matches}"
    # <<<<< RAW HTML CHECK <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_sideload_failure_rolls_back(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that sideload rolls back on require failure and namespace mismatch.

    1. Sideload a plugin whose body has BEGIN { die }; expect success=0.
       Assert file absent on container, Redis hash empty, namespace absent from plugin list.
    2. Sideload a plugin where file text declares namespace A but plugin_info returns B.
       Assert same rollback shape as subsection 1.
    """
    environment.setup(with_api_key=True)

    # >>>>> BROKEN REQUIRE >>>>>
    broken_ns = "test-sideload-broken-1"
    broken_pm_name = "TestSideloadBroken1.pm"
    broken_pm_body = (
        "package LANraragi::Plugin::Metadata::TestSideloadBroken1;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        "BEGIN { die 'boom' }\n"
        # namespace must be present for the pre-lock regex to extract it
        f"sub plugin_info {{ return ( name => 'test-broken', type => 'metadata', namespace => '{broken_ns}', version => '1.0' ); }}\n"
        "1;\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        pm_path = Path(tmpdir) / broken_pm_name
        pm_path.write_text(broken_pm_body, encoding="utf-8")
        status, content = await sideload_plugin(lrr_client, pm_path, DEFAULT_LRR_PASSWORD)
    assert status == 200, f"Expected HTTP 200 for failed sideload, got {status}"
    assert '"success":0' in content, f"Expected success=0 for broken sideload, got: {content}"

    broken_sideload_file = environment.plugin_sideloaded_dir / broken_pm_name
    assert not broken_sideload_file.exists(), (
        f"File should be absent after failed sideload: {broken_sideload_file}"
    )
    environment.redis_client.select(2)
    broken_redis_key = f"LRR_PLUGIN_{broken_ns.upper()}"
    broken_redis_hash = environment.redis_client.hgetall(broken_redis_key)
    assert not broken_redis_hash, (
        f"Expected empty Redis hash for {broken_ns} after failed sideload, got: {broken_redis_hash}"
    )
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list metadata plugins (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert broken_ns not in namespaces, f"{broken_ns} must be absent after failed sideload, got: {namespaces}"
    # <<<<< BROKEN REQUIRE <<<<<

    # >>>>> NAMESPACE MISMATCH >>>>>
    # file text declares mismatch-decl-ns but plugin_info returns the actual value below
    declared_ns = "test-sideload-mismatch-decl-1"
    actual_ns = "test-sideload-mismatch-actual-1"
    mismatch_pm_name = "TestSideloadMismatch1.pm"
    mismatch_pm_body = (
        "package LANraragi::Plugin::Metadata::TestSideloadMismatch1;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        # The pre-lock regex (Controller/Plugins.pm) is unanchored and matches
        # the first 'namespace => "..."' literal in the file text. This comment
        # makes the regex extract declared_ns; plugin_info() then returns
        # actual_ns, triggering the post-load coherence check.
        f"# namespace => '{declared_ns}'\n"
        "sub plugin_info {\n"
        "    return (\n"
        "        name      => 'test-mismatch',\n"
        "        type      => 'metadata',\n"
        f"        namespace => '{actual_ns}',\n"
        "        version   => '1.0',\n"
        "    );\n"
        "}\n"
        "sub get_tags { return (); }\n"
        "1;\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        pm_path = Path(tmpdir) / mismatch_pm_name
        pm_path.write_text(mismatch_pm_body, encoding="utf-8")
        status, content = await sideload_plugin(lrr_client, pm_path, DEFAULT_LRR_PASSWORD)
    assert status == 200, f"Expected HTTP 200 for failed sideload, got {status}"
    assert '"success":0' in content, f"Expected success=0 for mismatch sideload, got: {content}"

    mismatch_sideload_file = environment.plugin_sideloaded_dir / mismatch_pm_name
    assert not mismatch_sideload_file.exists(), (
        f"File should be absent after namespace-mismatch sideload: {mismatch_sideload_file}"
    )
    environment.redis_client.select(2)
    for ns_check in (declared_ns, actual_ns):
        ns_hash = environment.redis_client.hgetall(f"LRR_PLUGIN_{ns_check.upper()}")
        assert not ns_hash, (
            f"Expected empty Redis hash for {ns_check} after mismatch sideload, got: {ns_hash}"
        )
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list metadata plugins (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    for ns_check in (declared_ns, actual_ns):
        assert ns_check not in namespaces, (
            f"{ns_check} must be absent after namespace-mismatch sideload, got: {namespaces}"
        )
    # <<<<< NAMESPACE MISMATCH <<<<<

    # expect_no_error_logs is intentionally omitted: the sideload failure paths
    # emit LRR-side error logs by design (broken require, namespace mismatch).
    # Those logs are the expected diagnostic output, not a defect to flag.


@pytest.mark.asyncio
@pytest.mark.playwright
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_sideloaded_script_lifecycle(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test the end-to-end lifecycle of a sideloaded script plugin.

    1. Install sample-script from a registry as a managed plugin.
    2. Sideloading the same namespace while managed copy exists is rejected.
    3. Uninstall the managed sample-script.
    4. Sideload sample-script via UI upload.
    5. The plugin is recorded with a path relative to lib/, listed exactly once
       via API, rendered exactly once in the Manage tab with a sideloaded badge,
       and remains so after a server restart.
    6. Uninstall the sideloaded plugin via the API; provenance and on-disk file
       are cleaned up.
    """
    plugin_path = Path(__file__).parent.parent / "resources" / "plugins" / "scripts" / "SampleScript.pm"
    assert plugin_path.exists(), f"Test plugin file not found: {plugin_path}"

    # Single worker ensures all requests hit the same process, making per-worker
    # state bugs (stale symbol table after managed install/uninstall) deterministic.
    environment.setup(with_api_key=True, environment={"MOJO_WORKERS": "1"})

    # >>>>> INSTALL MANAGED SAMPLE-SCRIPT >>>>>
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

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_script_version = refresh_response.index["plugins"]["sample-script"]["channels"]["latest"]

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=sample_script_version)
    )
    assert not error, f"Failed to install sample-script (status {error.status}): {error.error}"
    # <<<<< INSTALL MANAGED SAMPLE-SCRIPT <<<<<

    # >>>>> SIDELOAD WHILE MANAGED COPY EXISTS IS REJECTED >>>>>
    status, content = await sideload_plugin(lrr_client, plugin_path, DEFAULT_LRR_PASSWORD)
    assert status == 200, f"Expected 200 upload status, got {status}: {content}"
    assert '"success":0' in content, f"Expected sideload to be rejected while managed copy exists, got: {content}"

    response, error = await lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="script"))
    assert not error, f"Failed to list scripts (status {error.status}): {error.error}"
    sample_scripts = [p for p in response.plugins if p.namespace == "sample-script"]
    assert len(sample_scripts) == 1, f"Expected one sample-script while managed copy exists, got {len(sample_scripts)}"
    # <<<<< SIDELOAD WHILE MANAGED COPY EXISTS IS REJECTED <<<<<

    # >>>>> SIDELOAD REPLACES MANAGED COPY >>>>>
    _, error = await lrr_client.misc_api.uninstall_plugin("sample-script")
    assert not error, f"Failed to uninstall managed sample-script (status {error.status}): {error.error}"

    status, content = await sideload_plugin(lrr_client, plugin_path, DEFAULT_LRR_PASSWORD)
    assert status == 200, f"Expected 200 upload status, got {status}: {content}"
    assert '"success":1' in content, f"Expected sideload to succeed after managed uninstall, got: {content}"
    # <<<<< SIDELOAD REPLACES MANAGED COPY <<<<<

    # >>>>> SIDELOAD PROVENANCE IS PORTABLE AND PERSISTS ACROSS RESTART >>>>>
    sideloaded_script_path = "LANraragi/Plugin/Sideloaded/SampleScript.pm"
    environment.redis_client.select(2)
    recorded_path = environment.redis_client.hget("LRR_PLUGIN_SAMPLE-SCRIPT", "installed_path")
    assert recorded_path == sideloaded_script_path, \
        f"Expected installed_path={sideloaded_script_path!r} after upload, got {recorded_path!r}"

    # >>>>> MANAGE TAB RENDERS ONE SIDELOADED ROW >>>>>
    # UI check runs BEFORE restart: the workers that handled install->uninstall->sideload
    # still have the managed class in their symbol table. This catches per-worker state
    # bugs (e.g. stale %INC entries causing the managed class to pass through get_plugins).
    async with playwright.async_api.async_playwright() as p:
        browser = await p.chromium.launch()
        bc = await browser.new_context()

        try:
            page = await bc.new_page()
            responses: list[playwright.async_api._generated.Response] = []
            console_evts: list[playwright.async_api._generated.ConsoleMessage] = []
            page.on("response", lambda response: responses.append(response))
            page.on("console", lambda console: console_evts.append(console))

            await page.goto(f"{lrr_client.lrr_base_url}/config/plugins")
            await page.wait_for_load_state("networkidle")
            if "login" in page.url.lower():
                await page.fill("#pw_field", DEFAULT_LRR_PASSWORD)
                await page.click("input[type='submit'][value='Login']")
                await page.wait_for_load_state("networkidle")
            responses.clear()
            console_evts.clear()

            # Fetch multiple times to exercise different Hypnotoad workers.
            for i in range(3):
                await page.goto(f"{lrr_client.lrr_base_url}/config/plugins#tab-manage")
                await page.wait_for_load_state("networkidle")

                sample_rows = page.locator(
                    '.manage-installed[data-type="script"] .manage-plugin-row[data-namespace="sample-script"]'
                )
                row_count = await sample_rows.count()
                assert row_count == 1, f"Fetch {i}: expected one sample-script row in Scripts section, got {row_count}"

                badge_text = await sample_rows.locator(".plugin-badge").text_content()
                assert badge_text == "sideloaded", f"Fetch {i}: expected 'sideloaded' badge, got: {badge_text!r}"

            await assert_browser_responses_ok(responses, lrr_client, logger=LOGGER)
            await assert_console_logs_ok(console_evts, lrr_client.lrr_base_url)
        finally:
            await bc.close()
            await browser.close()
    # <<<<< MANAGE TAB RENDERS ONE SIDELOADED ROW <<<<<

    # >>>>> SIDELOAD PROVENANCE PERSISTS ACROSS RESTART >>>>>
    environment.restart()

    environment.redis_client.select(2)
    recorded_path = environment.redis_client.hget("LRR_PLUGIN_SAMPLE-SCRIPT", "installed_path")
    assert recorded_path == sideloaded_script_path, \
        f"Expected installed_path={sideloaded_script_path!r} after restart, got {recorded_path!r}"

    response, error = await lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="script"))
    assert not error, f"Failed to list scripts after restart (status {error.status}): {error.error}"
    sample_scripts = [p for p in response.plugins if p.namespace == "sample-script"]
    assert len(sample_scripts) == 1, f"Expected one sample-script after restart, got {len(sample_scripts)}"
    # <<<<< SIDELOAD PROVENANCE PERSISTS ACROSS RESTART <<<<<

    # >>>>> UNINSTALL CLEARS PROVENANCE AND FILE >>>>>
    _, error = await lrr_client.misc_api.uninstall_plugin("sample-script")
    assert not error, f"Failed to uninstall sideloaded sample-script (status {error.status}): {error.error}"

    environment.redis_client.select(2)
    assert not environment.redis_client.hexists("LRR_PLUGIN_SAMPLE-SCRIPT", "installed_path"), \
        "Expected installed_path to be cleared after uninstall"
    # <<<<< UNINSTALL CLEARS PROVENANCE AND FILE <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_managed_plugin_upgrade_reloads_class(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that managed plugin upgrade reloads the class in the installing worker.

    The uploading worker has the plugin's source file cached in %INC. Without an
    explicit delete, require short-circuits and the new file contents are not
    loaded into the worker interpreter until server restart.

    1. Install sample-script from the main ref (version 1.0).
    2. Verify plugin_info returns version "1.0".
    3. Update the registry to the v1.1 ref (same namespace, version "1.1").
    4. Refresh and force-install sample-script.
    5. Verify plugin_info returns version "1.1" across multiple requests.
    """
    # Single worker deterministically routes the verification request to the
    # same process that handled the install/upgrade, where %INC is populated.
    environment.setup(with_api_key=True, environment={"MOJO_WORKERS": "1"})

    # >>>>> INSTALL v1.0 FROM main >>>>>
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

    main_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    main_version = main_refresh_response.index["plugins"]["sample-script"]["channels"]["latest"]

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=main_version)
    )
    assert not error, f"Failed to install sample-script v1.0 (status {error.status}): {error.error}"
    # <<<<< INSTALL v1.0 FROM main <<<<<

    # >>>>> VERIFY v1.0 IN LOADED CLASS >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="script")
    )
    assert not error, f"Failed to list scripts (status {error.status}): {error.error}"
    sample = next((p for p in response.plugins if p.namespace == "sample-script"), None)
    assert sample is not None, "sample-script not listed after install"
    assert sample.version == main_version, f"Expected v{main_version} after initial install, got {sample.version!r}"
    # <<<<< VERIFY v1.0 IN LOADED CLASS <<<<<

    # >>>>> SWITCH REGISTRY TO v1.1 AND UPGRADE >>>>>
    _, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(ref="v1.1")
    )
    assert not error, f"Failed to update registry ref (status {error.status}): {error.error}"

    v11_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry after ref change (status {error.status}): {error.error}"
    v11_version = v11_refresh_response.index["plugins"]["sample-script"]["channels"]["latest"]

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=v11_version, force=True)
    )
    assert not error, f"Failed to upgrade sample-script to v1.1 (status {error.status}): {error.error}"
    # <<<<< SWITCH REGISTRY TO v1.1 AND UPGRADE <<<<<

    # >>>>> VERIFY v1.1 IN LOADED CLASS >>>>>
    # Fire several reads to cover any transient scheduling; every one must see v1.1
    # because plugin_info() returns data from the in-memory class, which should have
    # been re-required against the new file contents.
    for attempt in range(5):
        response, error = await lrr_client.misc_api.get_available_plugins(
            GetAvailablePluginsRequest(type="script")
        )
        assert not error, f"Failed to list scripts (status {error.status}): {error.error}"
        sample = next((p for p in response.plugins if p.namespace == "sample-script"), None)
        assert sample is not None, f"sample-script not listed on attempt {attempt}"
        assert sample.version == v11_version, (
            f"Attempt {attempt}: loaded class still reports version {sample.version!r} "
            f"after upgrade; %INC short-circuited require so the new file was not re-read"
        )
    # <<<<< VERIFY v1.1 IN LOADED CLASS <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_managed_plugin_upgrade_reloads_across_workers(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that managed plugin upgrade reloads the class in every prefork worker.

    Each worker forks from master with the plugin's source file cached in %INC.
    Without cross-worker coherence, only the installing worker sees the new file
    after upgrade; other workers keep running the old symbols until restart.
    Round-robin routing exposes the inconsistency.

    1. Install sample-script v1.0 under default multi-worker prefork.
    2. Upgrade to v1.1 — run_script changes to prefix its result with "v1.1:".
    3. Fire use_plugin_sync across workers; assert every response reflects v1.1.
    """
    environment.setup(with_api_key=True)

    # >>>>> INSTALL v1.0 FROM main >>>>>
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

    main_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    main_version = main_refresh_response.index["plugins"]["sample-script"]["channels"]["latest"]

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=main_version)
    )
    assert not error, f"Failed to install sample-script v1.0 (status {error.status}): {error.error}"

    # Prime every prefork worker concurrently so each loads v1.0 into its own
    # %INC + symbol table. Concurrent requests force the client to open multiple
    # connections, spreading across workers. A serial keep-alive loop would pin
    # to a single worker and not reproduce the bug.
    prime_results = await asyncio.gather(*[
        lrr_client.misc_api.use_plugin(
            UsePluginRequest(plugin="sample-script", arg=f"prime-{i}")
        )
        for i in range(40)
    ])
    for i, (_, error) in enumerate(prime_results):
        assert not error, f"Prime attempt {i} failed (status {error.status}): {error.error}"
    # <<<<< INSTALL v1.0 FROM main <<<<<

    # >>>>> UPGRADE TO v1.1 >>>>>
    _, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(ref="v1.1")
    )
    assert not error, f"Failed to update registry ref (status {error.status}): {error.error}"

    v11_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry after ref change (status {error.status}): {error.error}"
    v11_version = v11_refresh_response.index["plugins"]["sample-script"]["channels"]["latest"]

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=v11_version, force=True)
    )
    assert not error, f"Failed to upgrade sample-script to v1.1 (status {error.status}): {error.error}"
    # <<<<< UPGRADE TO v1.1 <<<<<

    # >>>>> VERIFY v1.1 ACROSS WORKERS >>>>>
    # v1.1 run_script prefixes its result with "v1.1:". v1.0 returns the raw arg.
    # Concurrent requests spread across workers via the connection pool.
    verify_results = await asyncio.gather(*[
        lrr_client.misc_api.use_plugin(
            UsePluginRequest(plugin="sample-script", arg=f"ping-{i}")
        )
        for i in range(40)
    ])
    v10_responses = []
    for i, (response, error) in enumerate(verify_results):
        assert not error, f"Attempt {i}: use_plugin failed (status {error.status}): {error.error}"
        result = response.data.get("result") if response.data else None
        assert result is not None, f"Attempt {i}: use_plugin returned no result"
        if not result.startswith("v1.1:"):
            v10_responses.append((i, result))

    assert not v10_responses, (
        f"{len(v10_responses)} of 40 responses from stale workers still running v1.0 symbols: "
        f"{v10_responses[:5]}. Cross-worker coherence not converging after upgrade."
    )
    # <<<<< VERIFY v1.1 ACROSS WORKERS <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_managed_plugin_survives_restart(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that a managed plugin file persists across LRR restart and scan_plugins does not orphan it.

    1. Create registry, refresh, install sample-downloader -> 200.
    2. Capture installed_version and expected host path under plugin_managed_dir.
    3. Assert host path exists before restart.
    4. Restart LRR.
    5. Assert host path still exists after restart.
    6. GET download plugins -> sample-downloader present, registry provenance unchanged.
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

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_downloader_version = refresh_response.index["plugins"]["sample-downloader"]["channels"]["latest"]

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=sample_downloader_version)
    )
    assert not error, f"Failed to install sample-downloader (status {error.status}): {error.error}"
    installed_version = response.version
    installed_sha256 = response.installed_sha256
    # <<<<< SETUP AND INSTALL <<<<<

    # >>>>> RESTART >>>>>
    environment.restart()
    # <<<<< RESTART <<<<<

    # >>>>> ASSERT FILE AND PROVENANCE SURVIVE RESTART >>>>>
    plugin_file = environment.plugin_managed_dir / "Download" / "SampleDownload.pm"
    assert plugin_file.exists(), f"Expected plugin file at {plugin_file} after restart"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list download plugins after restart (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-downloader":
            assert plugin.installed_registry == reg_id, f"Expected provenance {reg_id} after restart, got: {plugin.installed_registry}"
            assert plugin.version == installed_version, (
                f"Expected version {installed_version!r} after restart, got: {plugin.version!r}"
            )
            assert plugin.installed_version == installed_version, (
                f"Expected installed_version {installed_version!r} after restart, got: {plugin.installed_version!r}"
            )
            assert plugin.installed_sha256 == installed_sha256, (
                f"Expected installed_sha256 {installed_sha256!r} after restart, got: {plugin.installed_sha256!r}"
            )
            assert plugin.installed_channel is None, (
                f"Expected no installed_channel after explicit install, got: {plugin.installed_channel!r}"
            )
            break
    else:
        pytest.fail("sample-downloader not found in download plugin list after restart")
    # <<<<< ASSERT FILE AND PROVENANCE SURVIVE RESTART <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_plugin_file_deleted_under_lrr(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that a managed plugin deleted from the filesystem is orphan-cleaned by scan_plugins at restart.

    1. Install sample-downloader from registry -> 200.
    2. Delete the plugin file directly from the host (plugin_managed_dir / "Download" / "SampleDownload.pm").
    3. Restart LRR (triggers scan_plugins).
    4. GET download plugins -> sample-downloader absent (orphan-clean removed provenance).
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

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_downloader_version = refresh_response.index["plugins"]["sample-downloader"]["channels"]["latest"]

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=sample_downloader_version)
    )
    assert not error, f"Failed to install sample-downloader (status {error.status}): {error.error}"
    # <<<<< SETUP AND INSTALL <<<<<

    # >>>>> DELETE PLUGIN FILE HOST-SIDE >>>>>
    plugin_file = environment.plugin_managed_dir / "Download" / "SampleDownload.pm"
    assert plugin_file.exists(), f"Expected plugin file at {plugin_file} before deletion"
    plugin_file.unlink()
    assert not plugin_file.exists(), "Plugin file should be gone after unlink"
    # <<<<< DELETE PLUGIN FILE HOST-SIDE <<<<<

    # >>>>> RESTART TRIGGERS ORPHAN CLEANUP >>>>>
    environment.restart()
    # <<<<< RESTART TRIGGERS ORPHAN CLEANUP <<<<<

    # >>>>> VERIFY PLUGIN ABSENT AFTER SCAN >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list download plugins after restart (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert "sample-downloader" not in namespaces, (
        f"sample-downloader should be orphan-cleaned after file deletion and restart, got: {namespaces}"
    )
    # <<<<< VERIFY PLUGIN ABSENT AFTER SCAN <<<<<

    expect_no_error_logs(environment, LOGGER)
