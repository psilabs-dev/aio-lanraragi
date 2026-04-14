"""
Plugin install/uninstall lifecycle integration tests.
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
#
#     1. Create registry, refresh index, and install sample-script.
#     2. Attempt to upload the sideloaded SampleScript.pm while managed copy exists, expect failure.
#     3. Uninstall the managed sample-script.
#     4. Upload the sideloaded SampleScript.pm, expect success.
#     5. Verify GET /api/plugins/script returns one sample-script entry.
#     """
#     plugin_path = Path(__file__).parent / "resources" / "plugins" / "scripts" / "SampleScript.pm"
#     assert plugin_path.exists(), f"Test plugin file not found: {plugin_path}"
#
#     environment.setup(with_api_key=True)
#
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
#
#     response, error = await lrr_client.misc_api.refresh_registry(reg_id)
#     assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"
#
#     response, error = await lrr_client.misc_api.install_plugin(
#         InstallPluginRequest(namespace="sample-script", registry=reg_id)
#     )
#     assert not error, f"Failed to install sample-script (status {error.status}): {error.error}"
#     # <<<<< SETUP REGISTRY AND INSTALL MANAGED SCRIPT <<<<<
#
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
#
#         with plugin_path.open("rb") as file_handle:
#             form_data = aiohttp.FormData(quote_fields=False)
#             form_data.add_field("file", file_handle, filename=plugin_path.name)
#             async with session.post(upload_url, data=form_data) as response:
#                 content = await response.text()
#                 assert response.status == 200, f"Expected 200 upload status, got {response.status}: {content}"
#                 assert '"success":0' in content, f"Expected failed duplicate upload, got: {content}"
#
#     response, error = await lrr_client.misc_api.get_available_plugins(
#         GetAvailablePluginsRequest(type="script")
#     )
#     assert not error, f"Failed to list scripts after duplicate upload (status {error.status}): {error.error}"
#     sample_scripts = [plugin for plugin in response.plugins if plugin.namespace == "sample-script"]
#     assert len(sample_scripts) == 1, f"Expected one sample-script before uninstall, got {len(sample_scripts)}"
#     # <<<<< DUPLICATE SIDELOAD UPLOAD FAILS <<<<<
#
#     # >>>>> UNINSTALL MANAGED SCRIPT >>>>>
#     response, error = await lrr_client.misc_api.uninstall_plugin("sample-script")
#     assert not error, f"Failed to uninstall sample-script (status {error.status}): {error.error}"
#     # <<<<< UNINSTALL MANAGED SCRIPT <<<<<
#
#     # >>>>> SIDELOAD UPLOAD SUCCEEDS >>>>>
#     async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
#         login_form = aiohttp.FormData(quote_fields=False)
#         login_form.add_field("password", DEFAULT_LRR_PASSWORD)
#         login_form.add_field("redirect", "index")
#         async with session.post(login_url, data=login_form) as response:
#             content = await response.text()
#             assert response.status == 200, f"Expected login redirect target to resolve with 200, got {response.status}"
#             assert "LANraragi" in content, f"Expected login flow to land on app page, got: {content}"
#
#         with plugin_path.open("rb") as file_handle:
#             form_data = aiohttp.FormData(quote_fields=False)
#             form_data.add_field("file", file_handle, filename=plugin_path.name)
#             async with session.post(upload_url, data=form_data) as response:
#                 content = await response.text()
#                 assert response.status == 200, f"Expected 200 upload status, got {response.status}: {content}"
#                 assert '"success":1' in content, f"Expected successful sideload upload, got: {content}"
#     # <<<<< SIDELOAD UPLOAD SUCCEEDS <<<<<
#
#     # >>>>> VERIFY SINGLE API ENTRY >>>>>
#     response, error = await lrr_client.misc_api.get_available_plugins(
#         GetAvailablePluginsRequest(type="script")
#     )
#     assert not error, f"Failed to list scripts after sideload upload (status {error.status}): {error.error}"
#     sample_scripts = [plugin for plugin in response.plugins if plugin.namespace == "sample-script"]
#     assert len(sample_scripts) == 1, f"Expected one sample-script after sideload replacement, got {len(sample_scripts)}"
#     # <<<<< VERIFY SINGLE API ENTRY <<<<<
#
#     expect_no_error_logs(environment, LOGGER)
