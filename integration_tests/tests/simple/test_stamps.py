import asyncio
import logging
import tempfile
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import ExtractArchiveRequest
from lanraragi.models.stamp import (
    AddStampRequest,
    DeleteStampRequest,
    GetStampedPagesRequest,
    GetStampsByPageRequest,
    UpdateStampRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import create_archive_file, upload_archive

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_stamp_crud(
    lrr_client: LRRClient, environment: AbstractLRRDeploymentContext, semaphore: asyncio.BoundedSemaphore,
):
    """
    Verify a stamp's lifecycle and that deleting it leaves another page's stamp intact.

    1. Upload and extract a two-page archive.
    2. Create a stamp on each page and verify the two pages are indexed.
    3. Update the first stamp and verify its content and position in the page's stamps.
    4. Delete it, verify that the first page is empty and the second stamp remains.
    """
    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive = create_archive_file(Path(tmpdir), "archive_1", 2)
        response, error = await upload_archive(lrr_client, archive, archive.name, semaphore)
        assert not error, f"Failed to upload archive: {error}"
        arcid = response.arcid

    response, error = await lrr_client.archive_api.extract_archive(ExtractArchiveRequest(arcid=arcid))
    assert not error, f"Failed to extract archive: {error}"
    assert len(response.pages) == 2
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> CREATE AND VERIFY STAMPS >>>>>
    stamp_ids = []
    for page in (1, 2):
        response, error = await lrr_client.stamp_api.add_stamp(
            AddStampRequest(arcid=arcid, index=page, content=f"stamp {page}", position="50,50")
        )
        assert not error, f"Failed to add stamp to page {page}: {error}"
        stamp_ids.append(response.stamp_id)

    first_stamp, second_stamp = stamp_ids
    assert first_stamp != second_stamp

    response, error = await lrr_client.stamp_api.get_stamped_pages(GetStampedPagesRequest(arcid=arcid))
    assert not error, f"Failed to get stamped pages: {error}"
    assert set(response.result) == {"1", "2"}
    # <<<<< CREATE AND VERIFY STAMPS <<<<<

    # >>>>> UPDATE STAMP >>>>>
    response, error = await lrr_client.stamp_api.update_stamp(
        UpdateStampRequest(stamp_id=first_stamp, content="updated", position="25,75")
    )
    assert not error, f"Failed to update stamp: {error}"

    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=1))
    assert not error, f"Failed to get stamps from page 1: {error}"
    assert len(response.result) == 1
    assert response.result[0].id == first_stamp
    assert response.result[0].content == "updated"
    assert response.result[0].position == "25,75"
    # <<<<< UPDATE STAMP <<<<<

    # >>>>> DELETE STAMP >>>>>
    response, error = await lrr_client.stamp_api.delete_stamp(DeleteStampRequest(stamp_id=first_stamp))
    assert not error, f"Failed to delete stamp: {error}"

    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=1))
    assert not error, f"Failed to get stamps from page 1: {error}"
    assert response.result == []

    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=2))
    assert not error, f"Failed to get stamps from page 2: {error}"
    assert len(response.result) == 1
    assert response.result[0].id == second_stamp

    response, error = await lrr_client.stamp_api.get_stamped_pages(GetStampedPagesRequest(arcid=arcid))
    assert not error, f"Failed to get stamped pages after deletion: {error}"
    assert response.result == ["2"]
    # <<<<< DELETE STAMP <<<<<

    expect_no_error_logs(environment, LOGGER)
