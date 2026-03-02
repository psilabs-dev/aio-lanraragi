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

from aio_lanraragi_tests.common import DEFAULT_API_KEY
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.deployment.factory import generate_deployment
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
    env: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    request.session.lrr_environments = {resource_prefix: env}
    yield env
    env.teardown(remove_data=True)


@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
    client = environment.lrr_client()
    client.update_api_key(DEFAULT_API_KEY)
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
    plugin_path = Path(__file__).parent / "resources" / "plugins" / "metadata" / "PrependAnnotatedToTitle.pm"
    plugin_namespace = "testannotatetitlemetadata"

    environment.setup(
        with_api_key=True,
        plugin_paths={"Metadata": [str(plugin_path.resolve())]},
    )

    assert plugin_path.exists(), f"Test plugin file not found: {plugin_path}"

    # >>>>> CHECK PLUGINS STAGE >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="metadata"))
    assert not error, f"Failed to get plugins (status {error.status}): {error.error}"
    plugin_namespaces = {plugin.namespace for plugin in response.plugins}
    assert plugin_namespace in plugin_namespaces, f"Missing test plugin namespace in metadata plugins: {plugin_namespace}"
    # <<<<< CHECK PLUGINS STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "test_plugin_prepend_title", num_pages=1)
        response, error = await upload_archive(
            lrr_client,
            archive_path,
            archive_path.name,
            asyncio.Semaphore(1),
            title="plugin title",
            tags="plugin:test",
        )
    assert not error, f"Upload failed (status {error.status}): {error.error}"
    arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> METADATA
    response, error = await lrr_client.archive_api.get_archive_metadata(GetArchiveMetadataRequest(arcid=arcid))
    assert not error, f"Failed to get metadata before plugin run (status {error.status}): {error.error}"
    assert response.title == "plugin title", f"Unexpected pre-plugin title: {response.title!r}"

    response, error = await lrr_client.misc_api.use_plugin(
        UsePluginRequest(plugin=plugin_namespace, arcid=arcid)
    )
    assert not error, f"Plugin execution failed (status {error.status}): {error.error}"
    assert response.type == "metadata", f"Unexpected plugin type: {response.type!r}"
    assert response.data is not None, "Plugin response did not include data payload"
    assert response.data.get("title") == "annotated plugin title", (
        f"Unexpected plugin response title: {response.data.get('title')!r}"
    )

    expect_no_error_logs(environment, LOGGER)

@pytest.mark.asyncio
async def test_plugin_not_available(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Test behavior of plugin when not available.

    According to pre-OpenAPI LRR, 200 status code would always be returned.
    """
    environment.setup(with_api_key=True)

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

@pytest.mark.asyncio
async def test_ignore_invalid_plugin_type(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext):
    """
    Plugins with an invalid plugin type are ignored by LRR.
    """
    plugin_path = Path(__file__).parent / "resources" / "plugins" / "metadata" / "InvalidPluginType.pm"
    environment.setup(
        with_api_key=True,
        plugin_paths={"Metadata": [str(plugin_path.resolve())]},
    )
    assert plugin_path.exists(), f"Test plugin file not found: {plugin_path}"

    # >>>>> CHECK PLUGINS STAGE >>>>>
    response, error = await lrr_client.misc_api.get_available_plugins(GetAvailablePluginsRequest(type="metadata"))
    assert not error, f"Failed to get plugins (status {error.status}): {error.error}"
    assert not response.plugins, f"Plugins is not empty: {response.plugins}"
    # <<<<< CHECK PLUGINS STAGE <<<<<
