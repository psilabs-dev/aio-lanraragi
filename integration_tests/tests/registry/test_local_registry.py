"""
Local-registry install error paths.
"""

import hashlib
import json
import logging
import time

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
    InstallPluginRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_local_registry_install_errors(
    environment: AbstractLRRDeploymentContext,
    lrr_client: LRRClient,
):
    """
    Test local-registry install error paths: malformed index, unsafe paths, sha256 mismatch, success.

    Single environment; registry.json is rotated between stages.

    1. Malformed plugins field: refresh 400.
    2. Traversal path: install 400, no file written.
    3. Absolute path: install 400, no file written.
    4. Wrong sha256: install 422, target path absent.
    5. Correct sha256: install 200, file present on host.
    """
    environment.setup(with_api_key=True)

    registry_json = environment.local_registry_dir / "registry.json"

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="local-test",
            type="local",
            path=environment.local_registry_path,
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    dummy_sha = "00" * 32

    # >>>>> MALFORMED PLUGINS FIELD >>>>>
    registry_json.write_text(json.dumps({"version": 1, "plugins": "not-an-object"}))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail with malformed plugins field"
    assert error.status == 400, f"Expected 400 for malformed plugins, got {error.status}"
    # <<<<< MALFORMED PLUGINS FIELD <<<<<

    # >>>>> TRAVERSAL PATH REJECTED >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "traversal-plugin": {
                "name": "traversal", "type": "download", "author": "test", "version": "1.0",
                "path": "../../etc/passwd", "sha256": dummy_sha,
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Expected refresh to succeed with traversal path entry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="traversal-plugin", registry=reg_id)
    )
    assert error is not None, "Expected install to fail for traversal path"
    assert error.status == 400, f"Expected 400 for traversal path install, got {error.status}"
    assert not list(environment.plugin_managed_dir.rglob("*.pm")), "No .pm files should be written for traversal path"
    # <<<<< TRAVERSAL PATH REJECTED <<<<<

    # >>>>> ABSOLUTE PATH REJECTED >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "absolute-plugin": {
                "name": "absolute", "type": "download", "author": "test", "version": "1.0",
                "path": "/etc/passwd", "sha256": dummy_sha,
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Expected refresh to succeed with absolute path entry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="absolute-plugin", registry=reg_id)
    )
    assert error is not None, "Expected install to fail for absolute path"
    assert error.status == 400, f"Expected 400 for absolute path install, got {error.status}"
    assert not list(environment.plugin_managed_dir.rglob("*.pm")), "No .pm files should be written for absolute path"
    # <<<<< ABSOLUTE PATH REJECTED <<<<<

    plugin_rel_path = "Plugin/Managed/Download/LocalSample.pm"
    plugin_file = environment.local_registry_dir / plugin_rel_path
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_text("""\
package LANraragi::Plugin::Managed::Download::LocalSample;

use strict;
use warnings;
no warnings 'uninitialized';

sub plugin_info {
    return (
        name      => "local-sample-downloader",
        type      => "download",
        namespace => "local-sample-downloader",
        author    => "test",
        version   => "1.0",
    );
}

1;
""", encoding="utf-8")
    real_sha = hashlib.sha256(plugin_file.read_bytes()).hexdigest()

    # >>>>> SHA256 MISMATCH REJECTED >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "local-sample-downloader": {
                "name": "Local Sample", "type": "download", "author": "test", "version": "1.0",
                "path": plugin_rel_path, "sha256": dummy_sha,
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Expected refresh to succeed with wrong sha entry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="local-sample-downloader", registry=reg_id)
    )
    assert error is not None, "Expected install to fail for wrong sha256"
    assert error.status == 422, f"Expected 422 for wrong sha256 install, got {error.status}"

    target_pm = environment.plugin_managed_dir / "Download" / "LocalSample.pm"
    assert not target_pm.exists(), f"Plugin file should not exist after sha256 mismatch: {target_pm}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="download")
    )
    assert not error, f"Failed to list plugins (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert "local-sample-downloader" not in namespaces, (
        f"Plugin should not appear in list after failed install: {namespaces}"
    )
    # <<<<< SHA256 MISMATCH REJECTED <<<<<

    # >>>>> SHA256 MATCH INSTALLS >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "local-sample-downloader": {
                "name": "Local Sample", "type": "download", "author": "test", "version": "1.0",
                "path": plugin_rel_path, "sha256": real_sha,
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Expected refresh to succeed (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="local-sample-downloader", registry=reg_id)
    )
    assert not error, f"Expected install to succeed (status {error.status}): {error.error}"
    assert response.namespace == "local-sample-downloader"
    assert response.version == "1.0", f"Expected version 1.0, got {response.version}"

    assert target_pm.exists(), f"Plugin file should exist after successful install: {target_pm}"

    expect_no_error_logs(environment, LOGGER)
    # <<<<< SHA256 MATCH INSTALLS <<<<<
