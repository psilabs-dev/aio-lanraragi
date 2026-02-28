"""
Plugin integration tests.

Plugins for testing purposes should be written under `tests/resources/plugins`.
"""

import asyncio
import logging
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest
import pytest_asyncio
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import GetArchiveMetadataRequest
from lanraragi.models.misc import GetAvailablePluginsRequest, UsePluginRequest

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive

LOGGER = logging.getLogger(__name__)
TEST_PLUGIN_NAMESPACE = "testannotatetitlemetadata"
TEST_PLUGIN_FILE = Path(__file__).parent / "resources" / "plugins" / "metadata" / "PrependAnnotatedToTitle.pm"


@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"


@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10


@pytest.fixture
def environment(request: pytest.FixtureRequest, resource_prefix: str, port_offset: int):
    env: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    try:
        env.setup(
            with_api_key=True,
            plugin_paths={"Metadata": [str(TEST_PLUGIN_FILE.resolve())]},
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
async def test_plugin_functionality(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Basic behavioral test with basic plugins.

    Uploads a real third-party metadata plugin to LRR, asserts availability and activeness.
    """
    assert TEST_PLUGIN_FILE.exists(), f"Test plugin file not found: {TEST_PLUGIN_FILE}"

    response, error = await lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="metadata"))
    assert not error, f"Failed to get plugins (status {error.status}): {error.error}"
    plugin_namespaces = {plugin.namespace for plugin in response.plugins}
    assert TEST_PLUGIN_NAMESPACE in plugin_namespaces, f"Missing test plugin namespace in metadata plugins: {TEST_PLUGIN_NAMESPACE}"

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_plugin_prepend_title", num_pages=1)
        upload_response, upload_error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            asyncio.Semaphore(1),
            title="plugin title",
            tags="plugin:test",
        )
    assert not upload_error, f"Upload failed (status {upload_error.status}): {upload_error.error}"

    arcid = upload_response.arcid
    assert arcid, "Upload did not return an archive id"

    pre_response, pre_error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    assert not pre_error, f"Failed to get metadata before plugin run (status {pre_error.status}): {pre_error.error}"
    assert pre_response.title == "plugin title", f"Unexpected pre-plugin title: {pre_response.title!r}"

    plugin_response, plugin_error = await lrr_client.misc_api.use_plugin(
        UsePluginRequest(plugin=TEST_PLUGIN_NAMESPACE, arcid=arcid)
    )
    assert not plugin_error, f"Plugin execution failed (status {plugin_error.status}): {plugin_error.error}"
    assert plugin_response.type == "metadata", f"Unexpected plugin type: {plugin_response.type!r}"
    assert plugin_response.data is not None, "Plugin response did not include data payload"
    assert plugin_response.data.get("title") == "annotated plugin title", (
        f"Unexpected plugin response title: {plugin_response.data.get('title')!r}"
    )

    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
async def test_plugin_not_available(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test behavior of plugin when not available.

    According to LRR, 200 status code would always be returned.
    """

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_plugin_prepend_title", num_pages=1)
        upload_response, upload_error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            asyncio.Semaphore(1),
            title="plugin title",
            tags="plugin:test",
        )
    assert not upload_error, f"Upload failed (status {upload_error.status}): {upload_error.error}"

    arcid = upload_response.arcid
    response, error = await lrr_client.misc_api.use_plugin(
        UsePluginRequest(plugin="does-not-exist", arcid=arcid)
    )
    assert response is None, "Expected no success response for unavailable plugin"
    assert error is not None, "Expected plugin error for unavailable plugin"
    assert error.status == 200, f"Expected status 200 from LRR plugin API, got {error.status}"
    assert error.error == "Plugin not found on system.", f"Unexpected plugin error message: {error.error!r}"
