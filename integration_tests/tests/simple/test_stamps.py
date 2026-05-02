"""
Integration tests for the Stamps API (PR #1493).
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.stamp import (
    AddStampRequest,
    DeleteStampRequest,
    GetStampedPagesRequest,
    GetStampRequest,
    GetStampsByPageRequest,
    UpdateStampRequest,
)

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    upload_archive,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.dev("stamps")
async def test_stamp_crud(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext, semaphore: asyncio.BoundedSemaphore):
    """
    Test stamp CRUD lifecycle and OpenAPI validation.

    1. Upload an archive.
    2. Add a stamp to page 0, verify stamp_id is returned as string.
    3. Get stamp by ID, verify fields.
    4. Get stamps by page, verify the stamp is present.
    5. Get stamped pages, verify page 0 is listed.
    6. Update the stamp content, verify success.
    7. Delete the stamp, verify success.
    8. Add a stamp with a non-existent archive ID, verify error.
    """
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect (status {error.status}): {error.error}"

    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "stamp_test", 3)
        response, error = await upload_archive(lrr_client, archive_path, "stamp_test.zip", semaphore)
        assert not error, f"Failed to upload archive (status {error.status}): {error.error}"
        arcid = response.arcid

    # add stamp
    response, error = await lrr_client.stamp_api.add_stamp(AddStampRequest(
        arcid=arcid, index=0, content="test stamp", position="50.0,50.0",
    ))
    assert not error, f"Failed to add stamp (status {error.status}): {error.error}"
    stamp_id = response.stamp_id
    assert isinstance(stamp_id, str), f"stamp_id should be str, got {type(stamp_id)}"
    LOGGER.info(f"Created stamp with id: {stamp_id}")

    # get stamp
    response, error = await lrr_client.stamp_api.get_stamp(GetStampRequest(
        arcid=arcid, stamp_id=stamp_id,
    ))
    assert not error, f"Failed to get stamp (status {error.status}): {error.error}"
    assert response.result.id == stamp_id
    assert response.result.content == "test stamp"
    assert response.result.position == "50.0,50.0"

    # get stamps by page
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(
        arcid=arcid, index=0,
    ))
    assert not error, f"Failed to get stamps by page (status {error.status}): {error.error}"
    assert len(response.result) == 1
    assert response.result[0].id == stamp_id

    # get stamped pages
    response, error = await lrr_client.stamp_api.get_stamped_pages(GetStampedPagesRequest(
        arcid=arcid,
    ))
    assert not error, f"Failed to get stamped pages (status {error.status}): {error.error}"
    assert "0" in response.result

    # update stamp
    response, error = await lrr_client.stamp_api.update_stamp(UpdateStampRequest(
        arcid=arcid, stamp_id=stamp_id, content="updated stamp",
    ))
    assert not error, f"Failed to update stamp (status {error.status}): {error.error}"

    # verify update
    response, error = await lrr_client.stamp_api.get_stamp(GetStampRequest(
        arcid=arcid, stamp_id=stamp_id,
    ))
    assert not error, f"Failed to get stamp after update (status {error.status}): {error.error}"
    assert response.result.content == "updated stamp"

    # delete stamp
    response, error = await lrr_client.stamp_api.delete_stamp(DeleteStampRequest(
        arcid=arcid, stamp_id=stamp_id,
    ))
    assert not error, f"Failed to delete stamp (status {error.status}): {error.error}"

    # verify deletion
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(
        arcid=arcid, index=0,
    ))
    assert not error, f"Failed to get stamps after delete (status {error.status}): {error.error}"
    assert len(response.result) == 0

    # add stamp with non-existent archive — confirms OpenAPI validation issue:
    # the server returns 200 with success=0 and stamp_id=0 (integer against string schema)
    # instead of a proper 400 error response.
    fake_arcid = "a" * 40
    response, error = await lrr_client.stamp_api.add_stamp(AddStampRequest(
        arcid=fake_arcid, index=0, content="should fail",
    ))
    if error:
        LOGGER.info(f"Non-existent archive stamp correctly returned error: status={error.status}")
    else:
        LOGGER.warning(f"Non-existent archive stamp returned success with stamp_id={response.stamp_id} — confirms finding #6")
        assert response.stamp_id == "0", f"Expected stamp_id '0' on failure path, got '{response.stamp_id}'"
