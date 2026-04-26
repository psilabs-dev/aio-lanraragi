"""
Local-registry validation, orphan, and install error paths.
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
    Test local-registry refresh validation and install error paths.

    Single environment; registry.json is rotated between stages.

    1. Malformed plugins field: refresh 400.
    2. Missing generated_at: refresh 400.
    3. Unknown plugin field: refresh 400.
    4. Invalid published_at: refresh 400.
    5. Invalid sha256 format: refresh 400.
    6. Traversal path: refresh 400.
    7. Absolute path: refresh 400.
    8. Symlink escape path: install 400.
    9. Wrong sha256: install 422, target path absent.
    10. Correct sha256: install 200, file present on host.
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

    # >>>>> MISSING generated_at >>>>>
    registry_json.write_text(json.dumps({"version": 1, "plugins": {}}))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail without generated_at"
    assert error.status == 400, f"Expected 400 for missing generated_at, got {error.status}"
    # <<<<< MISSING generated_at <<<<<

    # >>>>> UNKNOWN PLUGIN FIELD >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "unknown-field-plugin": {
                "namespace": "unknown-field-plugin",
                "type": "download",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "unknown",
                        "author": "test",
                        "description": "unknown field test plugin",
                        "artifact": "artifacts/unknown/1.0/Unknown.pm",
                        "sha256": dummy_sha,
                        "published_at": generated_at,
                    },
                },
                "unexpected": "boom",
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail on unknown plugin field"
    assert error.status == 400, f"Expected 400 for unknown plugin field, got {error.status}"
    # <<<<< UNKNOWN PLUGIN FIELD <<<<<

    # >>>>> INVALID published_at >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "bad-published-at": {
                "namespace": "bad-published-at",
                "type": "download",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "bad-published-at",
                        "author": "test",
                        "description": "bad published_at test plugin",
                        "artifact": "artifacts/bad-published-at/1.0/BadPublishedAt.pm",
                        "sha256": dummy_sha,
                        "published_at": "2026-04-25T10:00:00+01:00",
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail on invalid published_at"
    assert error.status == 400, f"Expected 400 for invalid published_at, got {error.status}"
    # <<<<< INVALID published_at <<<<<

    # >>>>> INVALID SHA256 FORMAT >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "bad-sha": {
                "namespace": "bad-sha",
                "type": "download",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "bad-sha",
                        "author": "test",
                        "description": "bad sha test plugin",
                        "artifact": "artifacts/bad-sha/1.0/BadSha.pm",
                        "sha256": "xyz",
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail on invalid sha256 format"
    assert error.status == 400, f"Expected 400 for invalid sha256 format, got {error.status}"
    # <<<<< INVALID SHA256 FORMAT <<<<<

    # >>>>> TRAVERSAL PATH REJECTED >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "traversal-plugin": {
                "namespace": "traversal-plugin",
                "type": "download",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "traversal",
                        "author": "test",
                        "description": "traversal test plugin",
                        "artifact": "../../etc/passwd",
                        "sha256": dummy_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail for traversal path"
    assert error.status == 400, f"Expected 400 for traversal path refresh, got {error.status}"
    assert not list(environment.plugin_managed_dir.rglob("*.pm")), "No .pm files should be written for traversal path"
    # <<<<< TRAVERSAL PATH REJECTED <<<<<

    # >>>>> ABSOLUTE PATH REJECTED >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "absolute-plugin": {
                "namespace": "absolute-plugin",
                "type": "download",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "absolute",
                        "author": "test",
                        "description": "absolute path test plugin",
                        "artifact": "/etc/passwd",
                        "sha256": dummy_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail for absolute path"
    assert error.status == 400, f"Expected 400 for absolute path refresh, got {error.status}"
    assert not list(environment.plugin_managed_dir.rglob("*.pm")), "No .pm files should be written for absolute path"
    # <<<<< ABSOLUTE PATH REJECTED <<<<<

    # >>>>> SYMLINK ESCAPE REJECTED >>>>>
    escape_target = environment.local_registry_dir.parent / "outside-plugin.pm"
    escape_target.write_text("outside", encoding="utf-8")
    symlink_path = environment.local_registry_dir / "artifacts" / "escape-link.pm"
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(escape_target)

    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "symlink-plugin": {
                "namespace": "symlink-plugin",
                "type": "download",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "symlink",
                        "author": "test",
                        "description": "symlink escape test plugin",
                        "artifact": "artifacts/escape-link.pm",
                        "sha256": dummy_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Expected refresh to succeed with symlink artifact entry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="symlink-plugin", registry=reg_id, version="1.0")
    )
    assert error is not None, "Expected install to fail for symlink escape"
    assert error.status == 400, f"Expected 400 for symlink escape install, got {error.status}"
    assert not list(environment.plugin_managed_dir.rglob("*.pm")), "No .pm files should be written for symlink escape"
    # <<<<< SYMLINK ESCAPE REJECTED <<<<<

    plugin_rel_path = "artifacts/local-sample-downloader/1.0/LocalSample.pm"
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

sub provide_url {
    return;
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
                "namespace": "local-sample-downloader",
                "type": "download",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "Local Sample",
                        "author": "test",
                        "description": "local sample downloader",
                        "artifact": plugin_rel_path,
                        "sha256": dummy_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Expected refresh to succeed with wrong sha entry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="local-sample-downloader", registry=reg_id, version="1.0")
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
                "namespace": "local-sample-downloader",
                "type": "download",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "Local Sample",
                        "author": "test",
                        "description": "local sample downloader",
                        "artifact": plugin_rel_path,
                        "sha256": real_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Expected refresh to succeed (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="local-sample-downloader", registry=reg_id, version="1.0")
    )
    assert not error, f"Expected install to succeed (status {error.status}): {error.error}"
    assert response.namespace == "local-sample-downloader"
    assert response.version == "1.0", f"Expected version 1.0, got {response.version}"
    assert response.installed_registry == reg_id, (
        f"Expected provenance {reg_id}, got {response.installed_registry}"
    )

    assert target_pm.exists(), f"Plugin file should exist after successful install: {target_pm}"

    expect_no_error_logs(environment, LOGGER)
    # <<<<< SHA256 MATCH INSTALLS <<<<<


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_install_blocked_against_default_namespace(
    environment: AbstractLRRDeploymentContext,
    lrr_client: LRRClient,
):
    """
    A registry plugin that claims a default plugin's namespace must be rejected,
    and force=true must not bypass the rejection.

    1. Create a local registry that publishes a plugin with namespace `copytags` (a default plugin).
    2. Refresh succeeds; install fails because the namespace is owned by a default plugin.
    3. Force install fails for the same reason.
    """
    environment.setup(with_api_key=True)

    plugin_rel_path = "artifacts/copytags-impostor/1.0/CopyTagsImpostor.pm"
    plugin_file = environment.local_registry_dir / plugin_rel_path
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_text("""\
package LANraragi::Plugin::Managed::Metadata::CopyTagsImpostor;

use strict;
use warnings;
no warnings 'uninitialized';

sub plugin_info {
    return (
        name      => "copytags-impostor",
        type      => "metadata",
        namespace => "copytags",
        author    => "test",
        version   => "1.0",
    );
}

sub get_tags { return (); }

1;
""", encoding="utf-8")
    real_sha = hashlib.sha256(plugin_file.read_bytes()).hexdigest()
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    registry_json = environment.local_registry_dir / "registry.json"
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "copytags": {
                "namespace": "copytags",
                "type": "metadata",
                "channels": {"latest": "1.0"},
                "versions": {
                    "1.0": {
                        "version": "1.0",
                        "name": "copytags-impostor",
                        "author": "test",
                        "description": "tries to shadow the built-in copytags plugin",
                        "artifact": plugin_rel_path,
                        "sha256": real_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="default-conflict",
            type="local",
            path=environment.local_registry_path,
        )
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="copytags", registry=reg_id, version="1.0")
    )
    assert error is not None, "Expected install to be rejected over a default plugin namespace"
    assert error.status == 400, f"Expected 400 for default-namespace conflict, got {error.status}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="copytags", registry=reg_id, version="1.0", force=True)
    )
    assert error is not None, "force=true must not bypass a default-plugin namespace conflict"
    assert error.status == 400, f"Expected 400 for default-namespace conflict (force), got {error.status}"

    target_pm = environment.plugin_managed_dir / "Metadata" / "CopyTagsImpostor.pm"
    assert not target_pm.exists(), f"Impostor plugin must not be written to disk: {target_pm}"

    expect_no_error_logs(environment, LOGGER)
