"""
Plugin install/uninstall lifecycle integration tests.
"""

import asyncio
import hashlib
import http
import json
import logging
import tempfile
import time
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient

# from lanraragi.models.archive import GetArchiveMetadataRequest
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
    InstallPluginRequest,
    # UpdateMetadataPluginConfigRequest,  # metadata-plugin feature removed; see test_plugin_config.py
    UpdateRegistryRequest,
    UsePluginRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)

# from aio_lanraragi_tests.utils.api_wrappers import (
#     create_archive_file,
#     upload_archive,
# )

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
    version_key = max(refresh_response.index["plugins"]["sample-downloader"]["versions"].keys())
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=version_key)
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
    sample = next((p for p in response.plugins if p.namespace == "sample-downloader"), None)
    assert sample is not None, "sample-downloader missing from download plugin list after install"
    assert sample.registry == reg_id, (
        f"Expected managed provenance {reg_id}, got: {sample.registry}"
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
async def test_plugin_install_provenance_roundtrip(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that registry, version, and sha256 provenance fields survive restart and explicit reinstall.

    1. Install sample-downloader, capture provenance from install response.
    2. Verify provenance fields in plugin list.
    3. Restart LRR, verify provenance fields survive.
    4. Uninstall and reinstall explicitly, verify provenance fields are preserved.
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

    version_key = max(refresh_response.index["plugins"]["sample-downloader"]["versions"].keys())
    version_record = refresh_response.index["plugins"]["sample-downloader"]["versions"][version_key]
    expected_sha = version_record["sha256"]

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=version_key)
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"
    assert response.registry == reg_id, f"Expected provenance {reg_id}, got: {response.registry}"
    assert response.sha256 == expected_sha, (
        f"Expected install sha256 {expected_sha}, got {response.sha256}"
    )

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    plugin = next((p for p in response.plugins if p.namespace == "sample-downloader"), None)
    assert plugin is not None, "sample-downloader missing from plugin list after install"
    assert plugin.registry == reg_id, f"Expected managed provenance {reg_id}, got: {plugin.registry}"
    assert plugin.version == version_key, (
        f"Expected version {version_key!r}, got {plugin.version!r}"
    )
    assert plugin.sha256 == expected_sha, (
        f"Expected sha256 {expected_sha}, got {plugin.sha256!r}"
    )

    environment.restart()

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins after restart (status {error.status}): {error.error}"
    plugin = next((p for p in response.plugins if p.namespace == "sample-downloader"), None)
    assert plugin is not None, "sample-downloader missing from plugin list after restart"
    assert plugin.registry == reg_id, (
        f"Expected provenance {reg_id} after restart, got {plugin.registry!r}"
    )
    assert plugin.version == version_key, (
        f"Expected version {version_key!r} after restart, got {plugin.version!r}"
    )
    assert plugin.sha256 == expected_sha, (
        f"Expected sha256 {expected_sha} after restart, got {plugin.sha256!r}"
    )

    response, error = await lrr_client.misc_api.uninstall_plugin("sample-downloader")
    assert not error, f"Failed to uninstall plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=version_key)
    )
    assert not error, f"Failed to reinstall plugin (status {error.status}): {error.error}"
    assert response.registry == reg_id, (
        f"Expected provenance {reg_id} after reinstall, got {response.registry!r}"
    )
    assert response.sha256 == expected_sha, (
        f"Expected sha256 {expected_sha} after reinstall, got {response.sha256}"
    )
    assert response.version == version_key, (
        f"Expected version {version_key!r} after reinstall, got {response.version!r}"
    )

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
    4. Install with empty version string, expect schema rejection.
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

    # >>>>> INSTALL EMPTY VERSION >>>>>
    # Pydantic does not constrain version length; send raw to confirm OpenAPI
    # rejects empty string via minLength: 1 before reaching the controller.
    status, content = await lrr_client.handle_request(
        http.HTTPMethod.POST,
        lrr_client.build_url("/api/plugins/install"),
        lrr_client.headers,
        json_data={
            "namespace": "sample-downloader",
            "registry": reg_id,
            "version": "",
        },
    )
    body = json.loads(content)
    assert status == 400, f"Expected 400 for empty version, got {status}: {body}"
    version_error = next((e for e in body.get("errors", []) if e.get("path") == "/body/version"), None)
    assert version_error is not None, f"Expected length violation on /body/version, got: {body}"
    # <<<<< INSTALL EMPTY VERSION <<<<<

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_plugin_install_failed_require_rolls_back(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that a managed install rolls back on require failure, for both fresh
    install and same-path upgrade.

    Fresh install:
    1. Create local registry with one broken plugin (valid Perl, BEGIN { die }).
    2. Install the broken plugin; expect a non-2xx error response.
    3. Assert file absent, Redis hash empty, namespace absent from listing.

    Upgrade (same-path):
    4. Register a second plugin with two versions sharing one package: 1.0.0 loadable, 1.1.0 BEGIN-die.
    5. Install 1.0.0; capture file bytes, Redis hash, listing entry.
    6. Install 1.1.0; expect non-2xx error.
    7. Assert prior 1.0.0 bytes preserved on disk, Redis hash unchanged, listing unchanged.
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
    plugin_rel_path = f"artifacts/{broken_ns}/1.0.0/{broken_pm_name}"

    upgrade_ns = "sample-upgrade-tx-1"
    upgrade_pm_name = "SampleUpgradeTx1.pm"
    upgrade_v1_body = (
        "package LANraragi::Plugin::Managed::Metadata::SampleUpgradeTx1;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        "sub plugin_info {\n"
        "    return (\n"
        "        name      => 'sample-upgrade-tx-1',\n"
        "        type      => 'metadata',\n"
        f"        namespace => '{upgrade_ns}',\n"
        "        author    => 'test',\n"
        "        version   => '1.0.0',\n"
        "    );\n"
        "}\n"
        "sub get_tags { return (); }\n"
        "1;\n"
    )
    upgrade_v2_body = (
        "package LANraragi::Plugin::Managed::Metadata::SampleUpgradeTx1;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        "BEGIN { die 'upgrade boom' }\n"
        "sub plugin_info {\n"
        "    return (\n"
        "        name      => 'sample-upgrade-tx-1',\n"
        "        type      => 'metadata',\n"
        f"        namespace => '{upgrade_ns}',\n"
        "        author    => 'test',\n"
        "        version   => '1.1.0',\n"
        "    );\n"
        "}\n"
        "sub get_tags { return (); }\n"
        "1;\n"
    )
    upgrade_v1_bytes = upgrade_v1_body.encode("utf-8")
    upgrade_v2_bytes = upgrade_v2_body.encode("utf-8")
    upgrade_v1_sha = hashlib.sha256(upgrade_v1_bytes).hexdigest()
    upgrade_v2_sha = hashlib.sha256(upgrade_v2_bytes).hexdigest()
    upgrade_v1_rel_path = f"artifacts/{upgrade_ns}/1.0.0/{upgrade_pm_name}"
    upgrade_v2_rel_path = f"artifacts/{upgrade_ns}/1.1.0/{upgrade_pm_name}"

    registry_data = {
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            broken_ns: {
                "namespace": broken_ns,
                "type": "metadata",
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "sample-broken-tx-1",
                        "author": "test",
                        "description": "broken require test plugin",
                        "artifact": plugin_rel_path,
                        "sha256": broken_sha,
                        "published_at": generated_at,
                    },
                },
            },
            upgrade_ns: {
                "namespace": upgrade_ns,
                "type": "metadata",
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "sample-upgrade-tx-1",
                        "author": "test",
                        "description": "good baseline for upgrade rollback test",
                        "artifact": upgrade_v1_rel_path,
                        "sha256": upgrade_v1_sha,
                        "published_at": generated_at,
                    },
                    "1.1.0": {
                        "version": "1.1.0",
                        "name": "sample-upgrade-tx-1",
                        "author": "test",
                        "description": "broken upgrade target for rollback test",
                        "artifact": upgrade_v2_rel_path,
                        "sha256": upgrade_v2_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }

    plugin_file = environment.local_registry_dir / plugin_rel_path
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_bytes(broken_pm_bytes)

    upgrade_v1_file = environment.local_registry_dir / upgrade_v1_rel_path
    upgrade_v1_file.parent.mkdir(parents=True, exist_ok=True)
    upgrade_v1_file.write_bytes(upgrade_v1_bytes)
    upgrade_v2_file = environment.local_registry_dir / upgrade_v2_rel_path
    upgrade_v2_file.parent.mkdir(parents=True, exist_ok=True)
    upgrade_v2_file.write_bytes(upgrade_v2_bytes)

    registry_json = environment.local_registry_dir / "registry.json"
    registry_json.write_text(json.dumps(registry_data), encoding="utf-8")

    # >>>>> SETUP REGISTRY >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="local-broken",
            provider="local",
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
        InstallPluginRequest(namespace=broken_ns, registry=reg_id, version="1.0.0")
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

    # >>>>> INSTALL UPGRADE BASELINE v1.0.0 AND CAPTURE STATE >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace=upgrade_ns, registry=reg_id, version="1.0.0")
    )
    assert not error, f"Failed to install upgrade baseline (status {error.status}): {error.error}"

    upgrade_target_pm = environment.plugin_managed_dir / "Metadata" / upgrade_pm_name
    assert upgrade_target_pm.exists(), f"Upgrade baseline file missing after install: {upgrade_target_pm}"
    captured_bytes = upgrade_target_pm.read_bytes()
    assert captured_bytes == upgrade_v1_bytes, "Upgrade baseline bytes do not match registry artifact"

    environment.redis_client.select(2)
    upgrade_redis_key = f"LRR_PLUGIN_{upgrade_ns.upper()}"
    captured_redis = environment.redis_client.hgetall(upgrade_redis_key)
    assert captured_redis, f"Expected non-empty Redis hash for {upgrade_ns} after baseline install"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list metadata plugins (status {error.status}): {error.error}"
    captured_listing = next((p for p in response.plugins if p.namespace == upgrade_ns), None)
    assert captured_listing is not None, f"{upgrade_ns} missing from listing after baseline install"
    # <<<<< INSTALL UPGRADE BASELINE v1.0.0 AND CAPTURE STATE <<<<<

    # >>>>> ATTEMPT UPGRADE TO BROKEN v1.1.0 >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace=upgrade_ns, registry=reg_id, version="1.1.0")
    )
    assert error is not None, "Expected error for broken upgrade install"
    assert error.status >= 400, f"Expected non-2xx status for broken upgrade install, got {error.status}"
    LOGGER.debug(f"Upgrade install: status={error.status}, error={error.error!r}")
    # <<<<< ATTEMPT UPGRADE TO BROKEN v1.1.0 <<<<<

    # >>>>> UPGRADE ROLLBACK ASSERTIONS >>>>>
    assert upgrade_target_pm.exists(), f"Prior artifact must remain on disk after failed upgrade: {upgrade_target_pm}"
    assert upgrade_target_pm.read_bytes() == captured_bytes, (
        "Prior artifact bytes were mutated by failed upgrade; spec requires restore to last working plugin"
    )

    environment.redis_client.select(2)
    after_redis = environment.redis_client.hgetall(upgrade_redis_key)
    assert after_redis == captured_redis, (
        f"Redis hash for {upgrade_ns} changed after failed upgrade.\nBefore: {captured_redis}\nAfter: {after_redis}"
    )

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list metadata plugins (status {error.status}): {error.error}"
    after_listing = next((p for p in response.plugins if p.namespace == upgrade_ns), None)
    assert after_listing == captured_listing, (
        f"Listing for {upgrade_ns} changed after failed upgrade.\nBefore: {captured_listing}\nAfter: {after_listing}"
    )
    # <<<<< UPGRADE ROLLBACK ASSERTIONS <<<<<

    response, error = await lrr_client.misc_api.uninstall_plugin(upgrade_ns)
    assert not error, f"Failed to uninstall upgrade baseline (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.delete_registry(reg_id)
    assert not error, f"Failed to delete registry (status {error.status}): {error.error}"

    # expect_no_error_logs is intentionally omitted: the install attempts against
    # deliberately broken plugins cause LRR to log server-side errors describing
    # the failed require/rollback. Those logs are expected, not defects.


@pytest.mark.asyncio
@pytest.mark.dev("registry")
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
    good_rel_path = f"artifacts/{good_ns}/1.0.0/{good_pm_name}"
    broken_rel_path = f"artifacts/{broken_ns}/1.0.0/{broken_pm_name}"

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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
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
            provider="local",
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
        InstallPluginRequest(namespace=good_ns, registry=reg_id, version="1.0.0")
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
        InstallPluginRequest(namespace=broken_ns, registry=reg_id, version="1.0.0")
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
async def test_server_restart_status(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test the server restart-pending flag across plugin install, upgrade, and uninstall.

    1. Fresh server reports restart_required False.
    2. First-time install keeps it False (no worker had the artifact loaded).
    3. Reinstalling the same namespace sets it True (workers may hold the prior code).
    4. Restarting the server clears it back to False.
    5. Uninstalling the plugin sets it True again.
    """
    environment.setup(with_api_key=True)

    plugin_ns = "sample-restart-1"
    plugin_pm_name = "SampleRestart1.pm"
    plugin_pm_body = (
        "package LANraragi::Plugin::Managed::Metadata::SampleRestart1;\n"
        "use strict;\n"
        "use warnings;\n"
        "no warnings 'uninitialized';\n"
        "sub plugin_info {\n"
        "    return (\n"
        "        name      => 'sample-restart-1',\n"
        "        type      => 'metadata',\n"
        f"        namespace => '{plugin_ns}',\n"
        "        author    => 'test',\n"
        "        version   => '1.0.0',\n"
        "    );\n"
        "}\n"
        "sub get_tags { return (); }\n"
        "1;\n"
    )
    plugin_bytes = plugin_pm_body.encode("utf-8")
    plugin_sha = hashlib.sha256(plugin_bytes).hexdigest()
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    plugin_rel_path = f"artifacts/{plugin_ns}/1.0.0/{plugin_pm_name}"

    registry_data = {
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            plugin_ns: {
                "namespace": plugin_ns,
                "type": "metadata",
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "sample-restart-1",
                        "author": "test",
                        "description": "restart-status test plugin",
                        "artifact": plugin_rel_path,
                        "sha256": plugin_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }
    plugin_file = environment.local_registry_dir / plugin_rel_path
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_bytes(plugin_bytes)
    registry_json = environment.local_registry_dir / "registry.json"
    registry_json.write_text(json.dumps(registry_data), encoding="utf-8")

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="local-restart", provider="local", path=environment.local_registry_path)
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

    # >>>>> FRESH SERVER >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {getattr(error, 'status', None)})"
    assert response.restart_required is False, "Fresh server should not report a pending restart"
    # <<<<< FRESH SERVER <<<<<

    # >>>>> FIRST INSTALL DOES NOT REQUIRE RESTART >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace=plugin_ns, registry=reg_id, version="1.0.0")
    )
    assert not error, f"Failed to install plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {getattr(error, 'status', None)})"
    assert response.restart_required is False, "First-time install should not require a restart"
    # <<<<< FIRST INSTALL DOES NOT REQUIRE RESTART <<<<<

    # >>>>> REINSTALL REQUIRES RESTART >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace=plugin_ns, registry=reg_id, version="1.0.0")
    )
    assert not error, f"Failed to reinstall plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {getattr(error, 'status', None)})"
    assert response.restart_required is True, "Reinstall of an already-registered plugin should require a restart"
    # <<<<< REINSTALL REQUIRES RESTART <<<<<

    # >>>>> RESTART CLEARS THE FLAG >>>>>
    environment.restart()
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {getattr(error, 'status', None)})"
    assert response.restart_required is False, "Restart should clear the pending-restart flag"
    # <<<<< RESTART CLEARS THE FLAG <<<<<

    # >>>>> UNINSTALL REQUIRES RESTART >>>>>
    response, error = await lrr_client.misc_api.uninstall_plugin(plugin_ns)
    assert not error, f"Failed to uninstall plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to get server info (status {getattr(error, 'status', None)})"
    assert response.restart_required is True, "Uninstall should set the pending-restart flag"
    # <<<<< UNINSTALL REQUIRES RESTART <<<<<

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
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    title_suffix_1_version = max(refresh_response.index["plugins"]["title-suffix-1"]["versions"].keys())
    # <<<<< SETUP REGISTRY <<<<<

    # >>>>> INSTALL >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="title-suffix-1", registry=reg_id, version=title_suffix_1_version)
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
        InstallPluginRequest(namespace="title-suffix-1", registry=reg_id, version=title_suffix_1_version)
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
    # Commented out: depended on metadata-plugin feature (update_metadata_plugin_config) removed
    # from dev-registry/backend. The reinstall lifecycle and orphaned-provenance assertions
    # below still exercise registry-side behavior.
    # response, error = await lrr_client.misc_api.update_metadata_plugin_config(
    #     "title-suffix-1", UpdateMetadataPluginConfigRequest(enabled=True)
    # )
    # assert not error, f"Failed to enable plugin (status {error.status}): {error.error}"
    #
    # with tempfile.TemporaryDirectory() as tmpdir:
    #     archive_path = create_archive_file(Path(tmpdir), "test_reinstall_exec", num_pages=1)
    #     response, error = await upload_archive(
    #         lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
    #         title="base", tags="test:reinstall",
    #     )
    # assert not error, f"Upload failed (status {error.status}): {error.error}"
    # arcid = response.arcid
    #
    # response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    # assert not error, f"Failed to get metadata (status {error.status}): {error.error}"
    # assert response.title == "base-1", f"Expected 'base-1' after enabled plugin execution, got: {response.title!r}"
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
    # with tempfile.TemporaryDirectory() as tmpdir:
    #     archive_path = create_archive_file(Path(tmpdir), "test_orphan_exec", num_pages=1)
    #     response, error = await upload_archive(
    #         lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
    #         title="orphan", tags="test:orphan",
    #     )
    # assert not error, f"Upload failed (status {error.status}): {error.error}"
    # arcid = response.arcid
    #
    # response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    # assert not error, f"Failed to get metadata (status {error.status}): {error.error}"
    # assert response.title == "orphan-1", f"Expected 'orphan-1' from orphaned plugin, got: {response.title!r}"
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
            'sub plugin_info { return ( name => "Conflict", namespace => "sample-metadata", type => "metadata" ); }\n'
            'sub get_tags { return (); }\n'
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
        sample_downloader_version = max(refresh_response.index["plugins"]["sample-downloader"]["versions"].keys())
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
        assert response.registry == reg_id, f"Expected provenance {reg_id}, got: {response.registry}"
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
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    sample_login_version = max(refresh_response.index["plugins"]["sample-login"]["versions"].keys())
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
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create reg A (status {error.status}): {error.error}"
    reg_a_id = response.id

    refresh_a_response, error = await lrr_client.misc_api.refresh_registry(reg_a_id)
    assert not error, f"Failed to refresh reg A (status {error.status}): {error.error}"
    sample_downloader_version = max(refresh_a_response.index["plugins"]["sample-downloader"]["versions"].keys())
    # <<<<< SETUP REG A <<<<<

    # >>>>> INSTALL FROM REG A >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_a_id, version=sample_downloader_version)
    )
    assert not error, f"Failed to install sample-downloader from reg A (status {error.status}): {error.error}"
    assert response.registry == reg_a_id, f"Expected provenance {reg_a_id}, got: {response.registry}"
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
    sample_downloader_version_b = max(refresh_b_response.index["plugins"]["sample-downloader"]["versions"].keys())
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
    assert response.registry == reg_b_id, f"Expected provenance {reg_b_id} after force install, got: {response.registry}"
    # <<<<< INSTALL FROM REG B WITH FORCE -> 200 <<<<<

    # >>>>> VERIFY PROVENANCE UPDATED >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list download plugins (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-downloader":
            assert plugin.registry == reg_b_id, f"Expected provenance {reg_b_id}, got: {plugin.registry}"
            break
    else:
        pytest.fail("sample-downloader not found in download plugin list after force install")
    # <<<<< VERIFY PROVENANCE UPDATED <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_managed_plugin_upgrade_reloads_class(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that managed plugin upgrade reloads the class metadata in every prefork worker.

    Each worker forks from master with the plugin's source file cached in %INC.
    Without cross-worker coherence, only the installing worker sees the new file
    after upgrade; other workers report stale plugin_info() until restart.
    Round-robin routing across the connection pool exposes the inconsistency.

    1. Install sample-script v1.0; prime every worker so each loads v1.0 into its %INC.
    2. Verify every response reports v1.0.
    3. Update the registry to v1.1, refresh, force-install.
    4. Fan out get_available_plugins concurrently; assert every response reports v1.1.
    """
    environment.setup(with_api_key=True)

    # >>>>> INSTALL v1.0 FROM main >>>>>
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

    main_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    main_version = max(main_refresh_response.index["plugins"]["sample-script"]["versions"].keys())

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=main_version)
    )
    assert not error, f"Failed to install sample-script v1.0 (status {error.status}): {error.error}"

    # Prime every prefork worker concurrently so each loads v1.0 into its own %INC.
    # Concurrent requests force the client to open multiple connections, spreading across workers.
    prime_results = await asyncio.gather(*[
        lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="script"))
        for _ in range(40)
    ])
    for i, (response, error) in enumerate(prime_results):
        assert not error, f"Prime attempt {i} failed (status {error.status}): {error.error}"
        sample = next((p for p in response.plugins if p.namespace == "sample-script"), None)
        assert sample is not None, f"Prime attempt {i}: sample-script not listed"
        assert sample.version == main_version, (
            f"Prime attempt {i}: expected v{main_version}, got {sample.version!r}"
        )
    # <<<<< INSTALL v1.0 FROM main <<<<<

    # >>>>> SWITCH REGISTRY TO v1.1 AND UPGRADE >>>>>
    _, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(ref="v1.1")
    )
    assert not error, f"Failed to update registry ref (status {error.status}): {error.error}"

    v11_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry after ref change (status {error.status}): {error.error}"
    v11_version = max(v11_refresh_response.index["plugins"]["sample-script"]["versions"].keys())

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=v11_version, force=True)
    )
    assert not error, f"Failed to upgrade sample-script to v1.1 (status {error.status}): {error.error}"
    # <<<<< SWITCH REGISTRY TO v1.1 AND UPGRADE <<<<<

    # >>>>> VERIFY v1.1 IN LOADED CLASS ACROSS WORKERS >>>>>
    # Fan out concurrent reads to spread across workers. Every response must report v1.1;
    # any stale v1.0 indicates a worker whose %INC short-circuited require after upgrade.
    verify_results = await asyncio.gather(*[
        lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="script"))
        for _ in range(40)
    ])
    stale_responses = []
    for i, (response, error) in enumerate(verify_results):
        assert not error, f"Verify attempt {i}: list scripts failed (status {error.status}): {error.error}"
        sample = next((p for p in response.plugins if p.namespace == "sample-script"), None)
        assert sample is not None, f"Verify attempt {i}: sample-script not listed"
        if sample.version != v11_version:
            stale_responses.append((i, sample.version))

    assert not stale_responses, (
        f"{len(stale_responses)} of 40 responses from stale workers still report v{main_version} "
        f"plugin_info after upgrade: {stale_responses[:5]}. %INC reload not converging."
    )
    # <<<<< VERIFY v1.1 IN LOADED CLASS ACROSS WORKERS <<<<<

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
            provider="github",
            url="https://github.com/psilabs-dev/lrr-plugins-demo.git",
            ref="main",
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    main_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    main_version = max(main_refresh_response.index["plugins"]["sample-script"]["versions"].keys())

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
    v11_version = max(v11_refresh_response.index["plugins"]["sample-script"]["versions"].keys())

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
async def test_managed_plugin_upgrade_reloads_across_workers_via_list_plugins(
    lrr_client: LRRClient,
    environment: AbstractLRRDeploymentContext,
):
    """
    User expectation: after upgrading a plugin from a registry, the plugin
    settings page (and any other UI listing plugins by type) reflects the
    new version immediately on every request, regardless of which prefork
    worker handles the listing call.

    This is the listing-path counterpart to
    `test_managed_plugin_upgrade_reloads_across_workers`. That sibling
    exercises the invocation path (`use_plugin`); this one exercises the
    listing path (`list_plugins` -> `get_plugins`), which is the path the
    settings page UI consumes.

    1. Install sample-script v1.0 from the demo registry under default multi-worker prefork.
    2. Prime every prefork worker via concurrent `list_plugins` calls so each
       loads the v1.0 class through the listing path.
    3. Upgrade to v1.1 (different ref publishes a new artifact bytes).
    4. Fire concurrent `list_plugins` calls; assert every worker reports v1.1.
       A worker that returns v1.0 indicates the listing path short-circuited
       on cached %INC without checking for an upgrade.
    """
    environment.setup(with_api_key=True)

    # >>>>> INSTALL v1.0 FROM main >>>>>
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

    main_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
    main_version = max(main_refresh_response.index["plugins"]["sample-script"]["versions"].keys())

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=main_version)
    )
    assert not error, f"Failed to install sample-script v1.0 (status {error.status}): {error.error}"
    # <<<<< INSTALL v1.0 FROM main <<<<<

    # >>>>> PRIME EVERY WORKER VIA list_plugins >>>>>
    # Concurrent listing requests force the client to open multiple connections,
    # spreading across workers. Each prefork worker loads the v1.0 class into
    # its own %INC + symbol table the first time it serves a list_plugins call
    # for the script type.
    prime_results = await asyncio.gather(*[
        lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="script"))
        for _ in range(40)
    ])
    for i, (response, error) in enumerate(prime_results):
        assert not error, f"Prime listing {i} failed (status {error.status}): {error.error}"
        sample = next((p for p in response.plugins if p.namespace == "sample-script"), None)
        assert sample is not None, f"sample-script missing from prime listing {i}"
        assert sample.version == main_version, (
            f"Prime listing {i}: expected v{main_version}, got {sample.version!r}"
        )
    # <<<<< PRIME EVERY WORKER VIA list_plugins <<<<<

    # >>>>> UPGRADE TO v1.1 >>>>>
    _, error = await lrr_client.misc_api.update_registry(
        reg_id, UpdateRegistryRequest(ref="v1.1")
    )
    assert not error, f"Failed to update registry ref (status {error.status}): {error.error}"

    v11_refresh_response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry after ref change (status {error.status}): {error.error}"
    v11_version = max(v11_refresh_response.index["plugins"]["sample-script"]["versions"].keys())
    assert v11_version != main_version, (
        f"Demo registry must publish a different version on the v1.1 ref; got {v11_version!r}"
    )

    _, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-script", registry=reg_id, version=v11_version, force=True)
    )
    assert not error, f"Failed to upgrade sample-script to v1.1 (status {error.status}): {error.error}"
    # <<<<< UPGRADE TO v1.1 <<<<<

    # >>>>> VERIFY v1.1 IN LISTING ACROSS WORKERS >>>>>
    verify_results = await asyncio.gather(*[
        lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="script"))
        for _ in range(40)
    ])
    stale_listings = []
    for i, (response, error) in enumerate(verify_results):
        assert not error, f"Verify listing {i} failed (status {error.status}): {error.error}"
        sample = next((p for p in response.plugins if p.namespace == "sample-script"), None)
        assert sample is not None, f"sample-script missing from verify listing {i}"
        if sample.version != v11_version:
            stale_listings.append((i, sample.version))

    assert not stale_listings, (
        f"{len(stale_listings)} of 40 list_plugins responses still report the old "
        f"version after upgrade: {stale_listings[:5]}. The listing path is "
        f"serving cached plugin_info() from %INC without checking for upgrades."
    )
    # <<<<< VERIFY v1.1 IN LISTING ACROSS WORKERS <<<<<

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
@pytest.mark.ratelimit
async def test_managed_plugin_survives_restart(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test that a managed plugin file persists across LRR restart and scan_plugins does not orphan it.

    Also exercises type self-heal: pre-PR Redis state lacks the `type` field; on restart,
    scan_plugins repopulates it from plugin_info() discovery.

    1. Create registry, refresh, install sample-downloader -> 200.
    2. Capture installed version and expected host path under plugin_managed_dir.
    3. Assert host path exists before restart.
    4. Delete the `type` field from Redis to simulate a pre-PR install.
    5. Restart LRR.
    6. Assert host path still exists after restart and `type` was self-healed to "download".
    7. GET download plugins -> sample-downloader present, registry provenance unchanged.
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
    sample_downloader_version = max(refresh_response.index["plugins"]["sample-downloader"]["versions"].keys())

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version=sample_downloader_version)
    )
    assert not error, f"Failed to install sample-downloader (status {error.status}): {error.error}"
    installed_version = response.version
    installed_sha256 = response.sha256
    # <<<<< SETUP AND INSTALL <<<<<

    # >>>>> SIMULATE PRE-PR STATE: TYPE FIELD ABSENT >>>>>
    environment.redis_client.select(2)
    assert environment.redis_client.hdel("LRR_PLUGIN_SAMPLE-DOWNLOADER", "type") == 1, (
        "Expected `type` field to exist before deletion"
    )
    # <<<<< SIMULATE PRE-PR STATE <<<<<

    # >>>>> RESTART >>>>>
    environment.restart()
    # <<<<< RESTART <<<<<

    # >>>>> ASSERT FILE AND PROVENANCE SURVIVE RESTART >>>>>
    plugin_file = environment.plugin_managed_dir / "Download" / "SampleDownload.pm"
    assert plugin_file.exists(), f"Expected plugin file at {plugin_file} after restart"

    environment.redis_client.select(2)
    healed_type = environment.redis_client.hget("LRR_PLUGIN_SAMPLE-DOWNLOADER", "type")
    assert healed_type == "download", f"Expected scan_plugins to self-heal type=download, got {healed_type!r}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list download plugins after restart (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "sample-downloader":
            assert plugin.registry == reg_id, f"Expected provenance {reg_id} after restart, got: {plugin.registry}"
            assert plugin.version == installed_version, (
                f"Expected version {installed_version!r} after restart, got: {plugin.version!r}"
            )
            assert plugin.sha256 == installed_sha256, (
                f"Expected sha256 {installed_sha256!r} after restart, got: {plugin.sha256!r}"
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
