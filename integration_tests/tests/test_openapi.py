"""
PR: https://github.com/Difegue/LANraragi/pull/1448

Integration tests for OpenAPI validation and validation bypass (LRR_DISABLE_OPENAPI).

These tests verify validation works, and that when OpenAPI validation bypass is enabled
(via env var or Redis config), requests that would normally be rejected by schema validation
pass through to the controller layer instead.
"""

import asyncio
import http
import json
import logging
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import GetArchiveMetadataRequest

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.log_parse import parse_lrr_logs
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"


@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10


@pytest.fixture
def environment(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int):
    """
    LRR environment with OpenAPI validation bypass enabled via env var.
    """
    env: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    try:
        env.setup(
            with_api_key=True,
            environment={"LRR_DISABLE_OPENAPI": "1"},
        )
        request.session.lrr_environments = {resource_prefix: env}
        yield env
    finally:
        env.teardown(remove_data=True)


@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.dev("openapi")
async def test_bypass_invalid_request(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    With bypass enabled, a malformed arcid that would normally be rejected by
    OpenAPI request validation (400 "String is too short") should pass through
    to the controller, which returns a business-logic error instead.

    Without bypass: 400 with {"errors": [...], "status": 400} (OpenAPI validation)
    With bypass:    400 with {"operation": "metadata", "success": 0, ...} (controller)
    """
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    # Malformed arcid "123" (requires 40-char hex) — bypasses validation, reaches controller
    status, content = await lrr_client.handle_request(
        http.HTTPMethod.GET, lrr_client.build_url("/api/archives/123"), lrr_client.headers
    )
    body = json.loads(content)

    # Controller returns its own 400 with operation/success structure, not the OpenAPI error structure
    assert status == 400, f"Expected 400 from controller business logic, got {status}"
    assert body["operation"] == "metadata", f"Expected operation 'metadata', got '{body.get('operation')}'"
    assert body["success"] == 0, f"Expected success=0, got {body.get('success')}"
    assert "errors" not in body, "Response should not contain OpenAPI 'errors' array"

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("openapi")
async def test_bypass_normal_request(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    With bypass enabled, normal valid API calls should behave identically
    to when validation is enabled.
    """
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"get_server_info failed (status {error.status}): {error.error}"

    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"get_all_archives failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Fresh server should have 0 archives, got {len(response.data)}"

    response, error = await lrr_client.category_api.get_all_categories()
    assert not error, f"get_all_categories failed (status {error.status}): {error.error}"

    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("openapi")
async def test_bypass_response_validation(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    With bypass enabled, a response that violates the OpenAPI schema (e.g. a
    non-boolean string in a boolean field) should still be returned to the client
    as-is, rather than being rejected by the plugin's response validator (500).

    Uploads an archive via API, then corrupts its `isnew` Redis field to a value
    that cannot be coerced to a JSON boolean, and queries the metadata endpoint.

    Without response bypass: 500 with {"errors": [{"message": "Expected boolean - got string."}]}
    With response bypass:    200 with the archive metadata (corrupted field passed through)
    """
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    # Upload an archive
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_bypass_response", num_pages=1)
        response, error = await upload_archive(
            lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
            title="Test Bypass Archive", tags="test:bypass",
        )
    assert not error, f"Upload failed (status {error.status}): {error.error}"
    arcid = response.arcid

    # Corrupt the isnew field in Redis db 0 to a non-coercible string
    r = environment.redis_client
    r.select(0)
    r.hset(arcid, "isnew", "not_a_boolean")

    status, content = await lrr_client.handle_request(
        http.HTTPMethod.GET, lrr_client.build_url(f"/api/archives/{arcid}"), lrr_client.headers
    )
    body = json.loads(content)

    # Response bypass active: corrupted data passes through, no 500
    assert status == 200, f"Expected 200 (bypass should skip response validation), got {status}. Body: {body}"
    assert body["arcid"] == arcid
    assert "errors" not in body, "Response should not contain OpenAPI validation errors"


@pytest.mark.asyncio
@pytest.mark.dev("openapi")
async def test_bypass_non_ascii_metadata(lrr_client: LRRClient):
    """
    With bypass enabled, ensure non-ASCII metadata is preserved in JSON responses.
    This specifically exercises the bypass render handler path.
    """
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    title = "日本語テスト"
    tags = "artist:作者名,series:シリーズ"

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_bypass_non_ascii", num_pages=1)
        response, error = await upload_archive(
            lrr_client, archive_path, archive_path.name, asyncio.Semaphore(1),
            title=title, tags=tags,
        )
    assert not error, f"Upload failed (status {error.status}): {error.error}"
    arcid = response.arcid

    response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    assert not error, f"Metadata request failed (status {error.status}): {error.error}"
    assert response.title == title, f"Title mismatch or mojibake: expected {title!r}, got {response.title!r}"
    assert "artist:作者名" in response.tags, f"Tags mismatch or mojibake: got {response.tags!r}"
    assert "series:シリーズ" in response.tags, f"Tags mismatch or mojibake: got {response.tags!r}"


@pytest.mark.asyncio
@pytest.mark.dev("openapi")
async def test_bypass_via_redis_config(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int):
    """
    Test that OpenAPI bypass can be enabled via Redis config key "disableopenapi"
    instead of the LRR_DISABLE_OPENAPI env var.

    1. LRR starts without bypass — validation rejects malformed request.
    2. Redis config enables bypass, LRR restarts — same request reaches controller.
    """
    env: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    try:
        # Start without any validation bypass
        env.setup(with_api_key=True)
        request.session.lrr_environments = {resource_prefix: env}

        client = env.lrr_client()
        try:
            # Validation active: malformed arcid returns OpenAPI error structure
            status, content = await client.handle_request(
                http.HTTPMethod.GET, client.build_url("/api/archives/123"), client.headers
            )
            body = json.loads(content)
            assert status == 400, f"Expected 400 from OpenAPI validation, got {status}"
            assert "errors" in body, f"Expected OpenAPI 'errors' array in response, got keys: {list(body.keys())}"
            error_messages = " ".join(e.get("message", "") for e in body["errors"])
            assert "String is too short" in error_messages, f"Expected 'String is too short' in errors, got: {error_messages}"
            expected_warning_message = 'OpenAPI >>> GET /api/archives/123 [{"message":"String is too short: 3\\/40.","path":"\\/id"}]'
            found_validation_warning = False
            lrr_logs = env.read_lrr_logs()
            mojo_logs = env.read_mojo_logs()
            for event in parse_lrr_logs(lrr_logs):
                if event.severity_level == "warn" and event.message == expected_warning_message:
                    found_validation_warning = True
                    break
            if not found_validation_warning:
                for event in parse_lrr_logs(mojo_logs):
                    if event.severity_level == "warn" and event.message == expected_warning_message:
                        found_validation_warning = True
                        break
            assert found_validation_warning, (
                "Expected exact OpenAPI validation warning in lanraragi.log or mojo.log, but it was not found. "
                f"expected={expected_warning_message!r}\n\n"
                f"full_lrr_logs:\n{lrr_logs}\n\n"
                f"full_mojo_logs:\n{mojo_logs}"
            )

            # Enable bypass and restart
            env.enable_openapi_bypass()
            env.restart()

            # Bypass active: same request reaches the controller
            status, content = await client.handle_request(
                http.HTTPMethod.GET, client.build_url("/api/archives/123"), client.headers
            )
            body = json.loads(content)
            assert status == 400, f"Expected 400 from controller business logic, got {status}"
            assert body["operation"] == "metadata", f"Expected operation 'metadata', got '{body.get('operation')}'"
            assert body["success"] == 0, f"Expected success=0, got {body.get('success')}"
            assert "errors" not in body, "Response should not contain OpenAPI 'errors' array"
        finally:
            await client.close()

        expect_no_error_logs(env, LOGGER)
    finally:
        env.teardown(remove_data=True)
