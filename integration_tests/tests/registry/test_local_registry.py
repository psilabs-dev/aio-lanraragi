"""
Local-registry validation, orphan, and install error paths.
"""

import asyncio
import hashlib
import json
import logging
import tempfile
import time
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.misc import (
    CreateRegistryRequest,
    GetAvailablePluginsRequest,
    InstallPluginRequest,
    UsePluginRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive

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
    6. Uppercase sha256: refresh 400 (canonical form is lowercase).
    7. Traversal path: refresh 400.
    8. Absolute path: refresh 400.
    9. Symlink escape path: install 400.
    10. Wrong sha256: install 422, target path absent.
    11. Correct sha256: install 200, file present on host.
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "unknown",
                        "author": "test",
                        "description": "unknown field test plugin",
                        "artifact": "artifacts/unknown/1.0.0/Unknown.pm",
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "bad-published-at",
                        "author": "test",
                        "description": "bad published_at test plugin",
                        "artifact": "artifacts/bad-published-at/1.0.0/BadPublishedAt.pm",
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "bad-sha",
                        "author": "test",
                        "description": "bad sha test plugin",
                        "artifact": "artifacts/bad-sha/1.0.0/BadSha.pm",
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

    # >>>>> UPPERCASE SHA256 REJECTED AT REFRESH >>>>>
    # User expectation (publisher-facing): LRR and registry publishers agree on a
    # single canonical lowercase form for SHA-256. A manifest that deviates is
    # rejected at refresh with a clear error, not silently accepted into the
    # cached index where it would later cause a misleading integrity-mismatch
    # at install time.
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "uppercase-sha": {
                "namespace": "uppercase-sha",
                "type": "download",
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "uppercase-sha",
                        "author": "test",
                        "description": "uppercase sha format test",
                        "artifact": "artifacts/uppercase-sha/1.0.0/UppercaseSha.pm",
                        "sha256": "AB" * 32,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail on uppercase sha256"
    assert error.status == 400, f"Expected 400 for uppercase sha256, got {error.status}"
    # <<<<< UPPERCASE SHA256 REJECTED AT REFRESH <<<<<

    # >>>>> UNKNOWN ROOT FIELD REJECTED AT REFRESH >>>>>
    # User expectation: registry manifests follow a strict schema. An unknown
    # field at the root level (e.g. a typo or an experimental publisher
    # extension) is rejected at refresh, not silently accepted.
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {},
        "generator": "publisher-tool-v9",
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail on unknown root field"
    assert error.status == 400, f"Expected 400 for unknown root field, got {error.status}"
    # <<<<< UNKNOWN ROOT FIELD REJECTED AT REFRESH <<<<<

    # >>>>> VERSION KEY DIVERGES FROM INNER VERSION REJECTED AT REFRESH >>>>>
    # User expectation: in a registry manifest, the version key (e.g. "1.0.0")
    # must equal the inner `version` field of that record. Otherwise the
    # version selected by `resolve_max_version` (which sorts on outer keys)
    # would not equal the version recorded in install provenance — admins
    # could not reliably reason about which version is installed.
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "version-key-mismatch": {
                "namespace": "version-key-mismatch",
                "type": "download",
                "versions": {
                    "1.0.0": {
                        "version": "9.9.9",
                        "name": "version-key-mismatch",
                        "author": "test",
                        "description": "outer key vs inner version mismatch",
                        "artifact": "artifacts/version-key-mismatch/1.0.0/VersionKeyMismatch.pm",
                        "sha256": dummy_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert error is not None, "Expected refresh to fail when version key != inner version"
    assert error.status == 400, f"Expected 400 for version-key/inner-version mismatch, got {error.status}"
    # <<<<< VERSION KEY DIVERGES FROM INNER VERSION REJECTED AT REFRESH <<<<<

    # >>>>> TRAVERSAL PATH REJECTED >>>>>
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "traversal-plugin": {
                "namespace": "traversal-plugin",
                "type": "download",
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
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
        InstallPluginRequest(namespace="symlink-plugin", registry=reg_id, version="1.0.0")
    )
    assert error is not None, "Expected install to fail for symlink escape"
    assert error.status == 400, f"Expected 400 for symlink escape install, got {error.status}"
    assert not list(environment.plugin_managed_dir.rglob("*.pm")), "No .pm files should be written for symlink escape"
    # <<<<< SYMLINK ESCAPE REJECTED <<<<<

    plugin_rel_path = "artifacts/local-sample-downloader/1.0.0/LocalSample.pm"
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
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
        InstallPluginRequest(namespace="local-sample-downloader", registry=reg_id, version="1.0.0")
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
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
        InstallPluginRequest(namespace="local-sample-downloader", registry=reg_id, version="1.0.0")
    )
    assert not error, f"Expected install to succeed (status {error.status}): {error.error}"
    assert response.namespace == "local-sample-downloader"
    assert response.version == "1.0.0", f"Expected version 1.0.0, got {response.version}"
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

    plugin_rel_path = "artifacts/copytags-impostor/1.0.0/CopyTagsImpostor.pm"
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
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
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
        InstallPluginRequest(namespace="copytags", registry=reg_id, version="1.0.0")
    )
    assert error is not None, "Expected install to be rejected over a default plugin namespace"
    assert error.status == 400, f"Expected 400 for default-namespace conflict, got {error.status}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="copytags", registry=reg_id, version="1.0.0", force=True)
    )
    assert error is not None, "force=true must not bypass a default-plugin namespace conflict"
    assert error.status == 400, f"Expected 400 for default-namespace conflict (force), got {error.status}"

    target_pm = environment.plugin_managed_dir / "Metadata" / "CopyTagsImpostor.pm"
    assert not target_pm.exists(), f"Impostor plugin must not be written to disk: {target_pm}"

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_install_blocked_against_invalid_filename(
    environment: AbstractLRRDeploymentContext,
    lrr_client: LRRClient,
):
    """
    User expectation: a registry publisher who ships an artifact whose
    filename contains characters outside the safe ASCII allowlist
    (spaces, exotic punctuation) is rejected at install time, before any
    bytes land in `Plugin/Managed/`. This protects deployments where
    spaces in plugin filenames cause subtle Perl module-load issues.

    1. Publish a plugin file at `My Plugin.pm` (space in filename) with
       a valid SHA-256 in the manifest.
    2. Refresh succeeds — manifest validation only checks for null bytes,
       absolute paths, and dot segments, none of which apply here.
    3. Install fails 422 with "Invalid plugin filename".
    4. No file lands in `Plugin/Managed/Download/`.
    """
    environment.setup(with_api_key=True)

    plugin_rel_path = "artifacts/filename-test/1.0.0/My Plugin.pm"
    plugin_file = environment.local_registry_dir / plugin_rel_path
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_text("""\
package LANraragi::Plugin::Managed::Download::SafePackage;

use strict;
use warnings;
no warnings 'uninitialized';

sub plugin_info {
    return (
        name      => "filename-test",
        type      => "download",
        namespace => "filename-test",
        author    => "test",
        version   => "1.0",
    );
}

sub provide_url { return; }

1;
""", encoding="utf-8")
    real_sha = hashlib.sha256(plugin_file.read_bytes()).hexdigest()
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    registry_json = environment.local_registry_dir / "registry.json"
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "filename-test": {
                "namespace": "filename-test",
                "type": "download",
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "filename-test",
                        "author": "test",
                        "description": "tests filename character allowlist",
                        "artifact": plugin_rel_path,
                        "sha256": real_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="filename-test", type="local", path=environment.local_registry_path)
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Refresh should accept the manifest (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="filename-test", registry=reg_id, version="1.0.0")
    )
    assert error is not None, "Expected install to fail for invalid filename"
    assert error.status == 422, f"Expected 422 for invalid filename, got {error.status}: {error.error}"
    assert "Invalid plugin filename" in (error.error or ""), (
        f"Expected error message to mention 'Invalid plugin filename', got: {error.error!r}. "
        f"A different rejection reason indicates the filename allowlist did not fire — install reached a later validation."
    )

    assert not list(environment.plugin_managed_dir.rglob("*.pm")), (
        "No .pm files should be written for an invalid-filename install"
    )

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_install_blocked_against_package_mismatch(
    environment: AbstractLRRDeploymentContext,
    lrr_client: LRRClient,
):
    """
    User expectation: a plugin file whose `package` declaration does not
    match the package implied by its artifact filename is rejected at
    install time. This prevents namespace squatting where a publisher
    ships `Foo.pm` declaring `LANraragi::Plugin::Managed::Download::Bar`
    and then later legitimate `Bar` plugins collide against the
    orphaned mismatched file.

    1. Publish a plugin at `Foo.pm` whose content declares
       `package LANraragi::Plugin::Managed::Download::Bar`.
    2. Refresh succeeds; sha256 verification at install time will pass.
    3. Install fails 422 with "Package mismatch".
    4. No file lands in `Plugin/Managed/Download/`.
    """
    environment.setup(with_api_key=True)

    plugin_rel_path = "artifacts/package-mismatch/1.0.0/Foo.pm"
    plugin_file = environment.local_registry_dir / plugin_rel_path
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_text("""\
package LANraragi::Plugin::Managed::Download::Bar;

use strict;
use warnings;
no warnings 'uninitialized';

sub plugin_info {
    return (
        name      => "package-mismatch",
        type      => "download",
        namespace => "package-mismatch",
        author    => "test",
        version   => "1.0",
    );
}

sub provide_url { return; }

1;
""", encoding="utf-8")
    real_sha = hashlib.sha256(plugin_file.read_bytes()).hexdigest()
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    registry_json = environment.local_registry_dir / "registry.json"
    registry_json.write_text(json.dumps({
        "version": 1,
        "generated_at": generated_at,
        "plugins": {
            "package-mismatch": {
                "namespace": "package-mismatch",
                "type": "download",
                "versions": {
                    "1.0.0": {
                        "version": "1.0.0",
                        "name": "package-mismatch",
                        "author": "test",
                        "description": "tests package vs filename mismatch detection",
                        "artifact": plugin_rel_path,
                        "sha256": real_sha,
                        "published_at": generated_at,
                    },
                },
            },
        },
    }))

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(name="package-mismatch", type="local", path=environment.local_registry_path)
    )
    assert not error, f"Failed to create registry (status {error.status}): {error.error}"
    reg_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg_id)
    assert not error, f"Refresh should accept the manifest (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="package-mismatch", registry=reg_id, version="1.0.0")
    )
    assert error is not None, "Expected install to fail for package mismatch"
    assert error.status == 422, f"Expected 422 for package mismatch, got {error.status}: {error.error}"
    assert "Package mismatch" in (error.error or ""), (
        f"Expected error message to mention 'Package mismatch', got: {error.error!r}. "
        f"A different rejection reason indicates the package check did not fire — install reached a later validation."
    )

    assert not list(environment.plugin_managed_dir.rglob("*.pm")), (
        "No .pm files should be written for a package-mismatch install"
    )

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_plugin_install_blocked_against_sideloaded(
    environment: AbstractLRRDeploymentContext,
    lrr_client: LRRClient,
):
    """
    User expectation: installing a managed plugin from a registry over an
    existing sideloaded plugin of the same namespace requires the user to
    remove the sideloaded plugin first, just like for builtin plugins.
    A registry install must not silently overwrite a sideloaded plugin,
    and `force=true` must not bypass this protection.

    1. Seed a sideloaded plugin (namespace `sample-downloader`) before LRR
       starts so it is discovered and registered as the existing copy.
    2. Confirm the plugin is registered with a `Plugin/Sideloaded/` path
       and no `installed_registry` provenance.
    3. Create a local registry that publishes namespace `sample-downloader`.
    4. Refresh succeeds; install without force is rejected with 400.
    5. Force install is rejected with the same status.
    6. Redis provenance for the sideloaded plugin is unchanged (still a
       Sideloaded path, still no `installed_registry`).
    7. No `Plugin/Managed/Download/SampleDownload.pm` is written.
    """
    sideloaded_body = """\
package LANraragi::Plugin::Sideloaded::Testing::SideSample;

use strict;
use warnings;
no warnings 'uninitialized';

sub plugin_info {
    return (
        name      => "sample-downloader-sideloaded",
        type      => "download",
        namespace => "sample-downloader",
        author    => "test",
        version   => "0.9",
    );
}

sub provide_url { return; }

1;
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        sideloaded_src = Path(tmpdir) / "SideSample.pm"
        sideloaded_src.write_bytes(sideloaded_body.encode("utf-8"))

        environment.setup(
            with_api_key=True,
            plugin_paths={"Sideloaded": [str(sideloaded_src)]},
        )

        sideloaded_redis_key = "LRR_PLUGIN_SAMPLE-DOWNLOADER"
        sideloaded_initial_path = environment.redis_client.hget(sideloaded_redis_key, "installed_path")
        assert sideloaded_initial_path and "Plugin/Sideloaded/" in sideloaded_initial_path, (
            f"Sideloaded fixture not registered with a Sideloaded path: {sideloaded_initial_path!r}"
        )
        assert environment.redis_client.hget(sideloaded_redis_key, "installed_registry") is None, (
            "Sideloaded fixture must not have installed_registry set"
        )

        plugin_rel_path = "artifacts/sample-downloader/1.0.0/SampleDownload.pm"
        plugin_file = environment.local_registry_dir / plugin_rel_path
        plugin_file.parent.mkdir(parents=True, exist_ok=True)
        plugin_file.write_text("""\
package LANraragi::Plugin::Managed::Download::SampleDownload;

use strict;
use warnings;
no warnings 'uninitialized';

sub plugin_info {
    return (
        name      => "sample-downloader",
        type      => "download",
        namespace => "sample-downloader",
        author    => "test",
        version   => "1.0",
    );
}

sub provide_url { return; }

1;
""", encoding="utf-8")
        real_sha = hashlib.sha256(plugin_file.read_bytes()).hexdigest()
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        registry_json = environment.local_registry_dir / "registry.json"
        registry_json.write_text(json.dumps({
            "version": 1,
            "generated_at": generated_at,
            "plugins": {
                "sample-downloader": {
                    "namespace": "sample-downloader",
                    "type": "download",
                    "versions": {
                        "1.0.0": {
                            "version": "1.0.0",
                            "name": "sample-downloader",
                            "author": "test",
                            "description": "managed sample downloader",
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
                name="sideloaded-conflict",
                type="local",
                path=environment.local_registry_path,
            )
        )
        assert not error, f"Failed to create registry (status {error.status}): {error.error}"
        reg_id = response.id

        response, error = await lrr_client.misc_api.refresh_registry(reg_id)
        assert not error, f"Failed to refresh registry (status {error.status}): {error.error}"

        # >>>>> INSTALL BLOCKED AGAINST SIDELOADED >>>>>
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version="1.0.0")
        )
        assert error is not None, "Expected install to be rejected over a sideloaded plugin"
        assert error.status == 400, (
            f"Expected 400 for sideloaded conflict, got {error.status}: {error.error}"
        )
        # <<<<< INSTALL BLOCKED AGAINST SIDELOADED <<<<<

        # >>>>> FORCE INSTALL ALSO BLOCKED >>>>>
        response, error = await lrr_client.misc_api.install_plugin(
            InstallPluginRequest(namespace="sample-downloader", registry=reg_id, version="1.0.0", force=True)
        )
        assert error is not None, "force=true must not bypass a sideloaded namespace conflict"
        assert error.status == 400, (
            f"Expected 400 for sideloaded conflict (force), got {error.status}: {error.error}"
        )
        # <<<<< FORCE INSTALL ALSO BLOCKED <<<<<

        # >>>>> SIDELOADED PROVENANCE UNTOUCHED, MANAGED ARTIFACT NOT WRITTEN >>>>>
        assert environment.redis_client.hget(sideloaded_redis_key, "installed_path") == sideloaded_initial_path, (
            "Sideloaded plugin's installed_path changed after rejected managed install"
        )
        assert environment.redis_client.hget(sideloaded_redis_key, "installed_registry") is None, (
            "Sideloaded plugin gained installed_registry after rejected managed install"
        )
        target_pm = environment.plugin_managed_dir / "Download" / "SampleDownload.pm"
        assert not target_pm.exists(), (
            f"Managed plugin must not be written when sideloaded conflict is present: {target_pm}"
        )
        # <<<<< SIDELOADED PROVENANCE UNTOUCHED, MANAGED ARTIFACT NOT WRITTEN <<<<<

        expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("registry")
async def test_composite_registry(
    environment: AbstractLRRDeploymentContext,
    lrr_client: LRRClient,
):
    """
    Test composite registry/plugin functionality. Use 3 local registries with metadata plugins.
    Tests cross-registry provenance handoff via force-install, max-version SemVer resolution, and version/registry-orphan transitions.

    Registry 1:
    - shared-metadata-1
        - v1.0.0 (appends " from registry 1 v1.0.0" to title)
        - v2.0.0 (appends " from registry 1 v2.0.0" to title)
    Registry 2:
    - shared-metadata-1
        - v1.0.0 (appends " from registry 2 v1.0.0" to title)
        - v1.1.0 (appends " from registry 2 v1.1.0" to title)
        - v2.0.0 (appends " from registry 2 v2.0.0" to title)
    Registry 3:
    - shared-metadata-1
        - v1.0.0 (appends " from registry 3 v1.0.0" to title)
        - v1.1.0 (appends " from registry 3 v1.1.0" to title)
        - v2.0.0 (appends " from registry 3 v2.0.0" to title)

    Default-registry designation is covered separately in test_default_registry.py.

    Steps:
    1. Add registry 1, registry 2, registry 3.
    2. Install shared-metadata-1 from registry 1 (max-version resolution selects v2.0.0).
    3. Expect shared-metadata-1 version is v2.0.0.
    4. Upload archive and invoke plugin, expect processed title.
    5. Reinstall same plugin/version with force, expect idempotent (provenance unchanged).
    6. Uninstall shared-metadata-1.
    7. Install shared-metadata-1:v1.0.0 from registry 2 (explicit version).
    8. Expect version: v1.0.0.
    9. Invoke plugin, expect processed title.
    10. Remove registry 2 (plugin now registry-orphaned), assert 2 registries total.
    11. Install v1.0.0 from registry 3 without force, expect provenance mismatch error.
    12. Install v1.0.0 from registry 3 with force; verify provenance updated.
    13. Test plugin run.
    14. Regenerate registry 3 without version 1.0.0 (plugin becomes version orphan).
    15. Install v2.0.0 and run plugin.

    Every installation -> assert values for provenance + configuration, and assert title after running plugin.
    `use_plugin` does not persist the returned title to the archive, so the stored title remains unchanged across runs.
    """
    environment.setup(with_api_key=True)

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def write_registry(reg_dir: Path, versions: list[tuple[str, int]], generated_at: str):
        plugins: dict = {}
        plugin_versions: dict = {}
        for version, registry_n in versions:
            rel_path = f"artifacts/shared-metadata-1/{version}/SharedMetadata1.pm"
            plugin_file = reg_dir / rel_path
            plugin_file.parent.mkdir(parents=True, exist_ok=True)
            suffix = f" from registry {registry_n} v{version}"
            plugin_file.write_text(f"""\
package LANraragi::Plugin::Managed::Metadata::SharedMetadata1;

use strict;
use warnings;
no warnings 'uninitialized';

sub plugin_info {{
    return (
        name        => "shared-metadata-1",
        type        => "metadata",
        namespace   => "shared-metadata-1",
        author      => "test",
        version     => "{version}",
        description => "shared-metadata-1 test plugin",
    );
}}

sub get_tags {{
    shift;
    my $lrr_info = shift;
    my $title = $lrr_info->{{archive_title}} . "{suffix}";
    return (title => $title);
}}

1;
""", encoding="utf-8")
            sha = hashlib.sha256(plugin_file.read_bytes()).hexdigest()
            plugin_versions[version] = {
                "version": version,
                "name": "shared-metadata-1",
                "author": "test",
                "description": f"shared metadata 1 v{version} from registry {registry_n}",
                "artifact": rel_path,
                "sha256": sha,
                "published_at": generated_at,
            }
        plugins["shared-metadata-1"] = {
            "namespace": "shared-metadata-1",
            "type": "metadata",
            "versions": plugin_versions,
        }
        (reg_dir / "registry.json").write_text(json.dumps({
            "version": 1,
            "generated_at": generated_at,
            "plugins": plugins,
        }), encoding="utf-8")

    reg1_dir = environment.local_registry_dir / "registry-1"
    reg2_dir = environment.local_registry_dir / "registry-2"
    reg3_dir = environment.local_registry_dir / "registry-3"

    reg1_dir.mkdir(parents=True, exist_ok=True)
    reg2_dir.mkdir(parents=True, exist_ok=True)
    reg3_dir.mkdir(parents=True, exist_ok=True)

    write_registry(reg1_dir, [("1.0.0", 1), ("2.0.0", 1)], generated_at)
    write_registry(reg2_dir, [("1.0.0", 2), ("1.1.0", 2), ("2.0.0", 2)], generated_at)
    write_registry(reg3_dir, [("1.0.0", 3), ("1.1.0", 3), ("2.0.0", 3)], generated_at)

    # >>>>> SETUP THREE REGISTRIES >>>>>
    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="registry-1",
            type="local",
            path=f"{environment.local_registry_path}/registry-1",
        )
    )
    assert not error, f"Failed to create registry 1 (status {error.status}): {error.error}"
    reg1_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg1_id)
    assert not error, f"Failed to refresh registry 1 (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="registry-2",
            type="local",
            path=f"{environment.local_registry_path}/registry-2",
        )
    )
    assert not error, f"Failed to create registry 2 (status {error.status}): {error.error}"
    reg2_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg2_id)
    assert not error, f"Failed to refresh registry 2 (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.create_registry(
        CreateRegistryRequest(
            name="registry-3",
            type="local",
            path=f"{environment.local_registry_path}/registry-3",
        )
    )
    assert not error, f"Failed to create registry 3 (status {error.status}): {error.error}"
    reg3_id = response.id

    response, error = await lrr_client.misc_api.refresh_registry(reg3_id)
    assert not error, f"Failed to refresh registry 3 (status {error.status}): {error.error}"
    # <<<<< SETUP THREE REGISTRIES <<<<<

    # >>>>> UPLOAD ARCHIVE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_composite_1", num_pages=1)
        response, error = await upload_archive(
            lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
            title="base title", tags="test:composite",
        )
    assert not error, f"Upload failed (status {error.status}): {error.error}"
    arcid = response.arcid
    # <<<<< UPLOAD ARCHIVE <<<<<

    # >>>>> MAX-VERSION INSTALL AND INVOKE FROM REGISTRY 1 >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="shared-metadata-1", registry=reg1_id)
    )
    assert not error, f"Failed to install from registry 1 (status {error.status}): {error.error}"
    assert response.version == "2.0.0", f"Expected max version 2.0.0, got {response.version}"
    assert response.installed_registry == reg1_id, (
        f"Expected installed_registry {reg1_id}, got {response.installed_registry}"
    )

    response, error = await lrr_client.misc_api.use_plugin(
        UsePluginRequest(plugin="shared-metadata-1", arcid=arcid)
    )
    assert not error, f"Plugin execution failed (status {error.status}): {error.error}"
    assert response.data is not None, "Plugin response did not include data payload"
    assert response.data.get("title") == "base title from registry 1 v2.0.0", (
        f"Unexpected title after registry 1 v2.0.0 run: {response.data.get('title')!r}"
    )
    # <<<<< MAX-VERSION INSTALL AND INVOKE FROM REGISTRY 1 <<<<<

    # >>>>> IDEMPOTENT FORCE REINSTALL >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins before reinstall (status {error.status}): {error.error}"
    pre_reinstall_version = None
    pre_reinstall_sha256 = None
    pre_reinstall_registry = None
    for plugin in response.plugins:
        if plugin.namespace == "shared-metadata-1":
            pre_reinstall_version = plugin.installed_version
            pre_reinstall_sha256 = plugin.installed_sha256
            pre_reinstall_registry = plugin.installed_registry
            break
    else:
        pytest.fail("shared-metadata-1 not found before force reinstall")

    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="shared-metadata-1", registry=reg1_id, version="2.0.0", force=True)
    )
    assert not error, f"Force reinstall failed (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after reinstall (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "shared-metadata-1":
            assert plugin.installed_version == pre_reinstall_version, "installed_version changed after force reinstall"
            assert plugin.installed_sha256 == pre_reinstall_sha256, "installed_sha256 changed after force reinstall"
            assert plugin.installed_registry == pre_reinstall_registry, "installed_registry changed after force reinstall"
            break
    else:
        pytest.fail("shared-metadata-1 not found after force reinstall")
    # <<<<< IDEMPOTENT FORCE REINSTALL <<<<<

    # >>>>> UNINSTALL >>>>>
    response, error = await lrr_client.misc_api.uninstall_plugin("shared-metadata-1")
    assert not error, f"Failed to uninstall plugin (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after uninstall (status {error.status}): {error.error}"
    namespaces = {p.namespace for p in response.plugins}
    assert "shared-metadata-1" not in namespaces, (
        f"Plugin still listed after uninstall: {namespaces}"
    )
    # <<<<< UNINSTALL <<<<<

    # >>>>> EXPLICIT VERSION INSTALL AND INVOKE FROM REGISTRY 2 >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="shared-metadata-1", registry=reg2_id, version="1.0.0")
    )
    assert not error, f"Failed to install v1.0.0 from registry 2 (status {error.status}): {error.error}"
    assert response.version == "1.0.0", f"Expected version 1.0.0, got {response.version}"
    assert response.installed_registry == reg2_id, (
        f"Expected installed_registry {reg2_id}, got {response.installed_registry}"
    )

    response, error = await lrr_client.misc_api.use_plugin(
        UsePluginRequest(plugin="shared-metadata-1", arcid=arcid)
    )
    assert not error, f"Plugin execution failed (status {error.status}): {error.error}"
    assert response.data is not None, "Plugin response did not include data payload"
    assert response.data.get("title") == "base title from registry 2 v1.0.0", (
        f"Unexpected title after registry 2 v1.0.0 run: {response.data.get('title')!r}"
    )

    # <<<<< EXPLICIT VERSION INSTALL AND INVOKE FROM REGISTRY 2 <<<<<

    # >>>>> REGISTRY-ORPHAN (DELETE REGISTRY 2) >>>>>
    response, error = await lrr_client.misc_api.delete_registry(reg2_id)
    assert not error, f"Failed to delete registry 2 (status {error.status}): {error.error}"

    response, error = await lrr_client.misc_api.list_registries()
    assert not error, f"Failed to list registries (status {error.status}): {error.error}"
    assert len(response.registries) == 2, (
        f"Expected 2 registries after deleting registry 2, got {len(response.registries)}"
    )

    # provenance still queryable even though registry is gone
    response, error = await lrr_client.misc_api.get_available_plugins(
        GetAvailablePluginsRequest(type="metadata")
    )
    assert not error, f"Failed to list plugins after registry delete (status {error.status}): {error.error}"
    for plugin in response.plugins:
        if plugin.namespace == "shared-metadata-1":
            assert plugin.installed_registry == reg2_id, (
                f"Expected orphaned provenance {reg2_id}, got {plugin.installed_registry}"
            )
            break
    else:
        pytest.fail("shared-metadata-1 should still be listed after registry delete")
    # <<<<< REGISTRY-ORPHAN (DELETE REGISTRY 2) <<<<<

    # >>>>> CROSS-REGISTRY WITHOUT FORCE (400) >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="shared-metadata-1", registry=reg3_id, version="1.0.0")
    )
    assert error is not None, "Expected 400 for cross-registry install without force"
    assert error.status == 400, f"Expected 400 for cross-registry conflict, got {error.status}"
    # <<<<< CROSS-REGISTRY WITHOUT FORCE (400) <<<<<

    # >>>>> CROSS-REGISTRY WITH FORCE AND INVOKE >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="shared-metadata-1", registry=reg3_id, version="1.0.0", force=True)
    )
    assert not error, f"Failed to force install from registry 3 (status {error.status}): {error.error}"
    assert response.installed_registry == reg3_id, (
        f"Expected installed_registry {reg3_id}, got {response.installed_registry}"
    )

    response, error = await lrr_client.misc_api.use_plugin(
        UsePluginRequest(plugin="shared-metadata-1", arcid=arcid)
    )
    assert not error, f"Plugin execution failed (status {error.status}): {error.error}"
    assert response.data is not None, "Plugin response did not include data payload"
    assert response.data.get("title") == "base title from registry 3 v1.0.0", (
        f"Unexpected title after registry 3 v1.0.0 run: {response.data.get('title')!r}"
    )

    # <<<<< CROSS-REGISTRY WITH FORCE AND INVOKE <<<<<

    # >>>>> VERSION-ORPHAN (DROP v1.0.0 FROM REGISTRY 3) >>>>>
    # rewrite registry-3 to list only v1.1.0 and v2.0.0; v1.0.0 is gone
    write_registry(reg3_dir, [("1.1.0", 3), ("2.0.0", 3)], generated_at)

    response, error = await lrr_client.misc_api.refresh_registry(reg3_id)
    assert not error, f"Failed to refresh registry 3 after version drop (status {error.status}): {error.error}"
    # <<<<< VERSION-ORPHAN (DROP v1.0.0 FROM REGISTRY 3) <<<<<

    # >>>>> UPGRADE FROM VERSION-ORPHAN STATE AND INVOKE >>>>>
    response, error = await lrr_client.misc_api.install_plugin(
        InstallPluginRequest(namespace="shared-metadata-1", registry=reg3_id, version="2.0.0", force=True)
    )
    assert not error, f"Failed to install v2.0.0 from registry 3 (status {error.status}): {error.error}"
    assert response.version == "2.0.0", f"Expected version 2.0.0, got {response.version}"
    assert response.installed_registry == reg3_id, (
        f"Expected installed_registry {reg3_id}, got {response.installed_registry}"
    )

    response, error = await lrr_client.misc_api.use_plugin(
        UsePluginRequest(plugin="shared-metadata-1", arcid=arcid)
    )
    assert not error, f"Plugin execution failed (status {error.status}): {error.error}"
    assert response.data is not None, "Plugin response did not include data payload"
    assert response.data.get("title") == "base title from registry 3 v2.0.0", (
        f"Unexpected title after registry 3 v2.0.0 run: {response.data.get('title')!r}"
    )

    # <<<<< UPGRADE FROM VERSION-ORPHAN STATE AND INVOKE <<<<<

    expect_no_error_logs(environment, LOGGER)
