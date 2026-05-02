"""
Integration tests for the Stamps API (PR #1493).
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import DeleteArchiveRequest
from lanraragi.models.stamp import (
    AddStampRequest,
    DeleteStampRequest,
    GetStampedPagesRequest,
    GetStampRequest,
    GetStampsByPageRequest,
    UpdateStampRequest,
)

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    upload_archive,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.dev("stamps")
async def test_stamp_crud(lrr_client: LRRClient, environment: AbstractLRRDeploymentContext, semaphore: asyncio.BoundedSemaphore):
    """
    Happy-path lifecycle for a single stamp on one archive.

    1. Upload an archive with 3 pages.
    2. Add a stamp to page 0; verify stamp_id is a string.
    3. Get the stamp by id; verify content/position fields.
    4. Get stamps by page 0; verify the stamp is present.
    5. Get stamped pages; verify "0" is listed.
    6. Update the stamp content; verify the change is observable on a subsequent get_stamp.
    7. Delete the stamp; verify it disappears from get_stamps_by_page.
    """
    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "stamp_test", 3)
        response, error = await upload_archive(lrr_client, archive_path, "stamp_test.zip", semaphore)
        assert not error, f"Failed to upload archive (status {error.status}): {error.error}"
        arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> ADD STAMP >>>>>
    response, error = await lrr_client.stamp_api.add_stamp(AddStampRequest(
        arcid=arcid, index=0, content="test stamp", position="50.0,50.0",
    ))
    assert not error, f"Failed to add stamp (status {error.status}): {error.error}"
    stamp_id = response.stamp_id
    assert isinstance(stamp_id, str), f"stamp_id should be str, got {type(stamp_id)}"
    LOGGER.debug(f"Created stamp with id: {stamp_id}")
    del response, error
    # <<<<< ADD STAMP <<<<<

    # >>>>> GET STAMP >>>>>
    response, error = await lrr_client.stamp_api.get_stamp(GetStampRequest(stamp_id=stamp_id))
    assert not error, f"Failed to get stamp (status {error.status}): {error.error}"
    assert response.result.id == stamp_id
    assert response.result.content == "test stamp"
    assert response.result.position == "50.0,50.0"
    del response, error
    # <<<<< GET STAMP <<<<<

    # >>>>> GET STAMPS BY PAGE >>>>>
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=0))
    assert not error, f"Failed to get stamps by page (status {error.status}): {error.error}"
    assert len(response.result) == 1
    assert response.result[0].id == stamp_id
    del response, error
    # <<<<< GET STAMPS BY PAGE <<<<<

    # >>>>> GET STAMPED PAGES >>>>>
    response, error = await lrr_client.stamp_api.get_stamped_pages(GetStampedPagesRequest(arcid=arcid))
    assert not error, f"Failed to get stamped pages (status {error.status}): {error.error}"
    assert "0" in response.result
    del response, error
    # <<<<< GET STAMPED PAGES <<<<<

    # >>>>> UPDATE STAMP >>>>>
    response, error = await lrr_client.stamp_api.update_stamp(UpdateStampRequest(
        stamp_id=stamp_id, content="updated stamp",
    ))
    assert not error, f"Failed to update stamp (status {error.status}): {error.error}"
    response, error = await lrr_client.stamp_api.get_stamp(GetStampRequest(stamp_id=stamp_id))
    assert not error, f"Failed to get stamp after update (status {error.status}): {error.error}"
    assert response.result.content == "updated stamp"
    del response, error
    # <<<<< UPDATE STAMP <<<<<

    # >>>>> DELETE STAMP >>>>>
    response, error = await lrr_client.stamp_api.delete_stamp(DeleteStampRequest(stamp_id=stamp_id))
    assert not error, f"Failed to delete stamp (status {error.status}): {error.error}"
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=0))
    assert not error, f"Failed to get stamps after delete (status {error.status}): {error.error}"
    assert len(response.result) == 0
    del response, error
    # <<<<< DELETE STAMP <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("stamps")
async def test_stamp_get_update_delete_missing_stamp_id(
    lrr_client: LRRClient, environment: AbstractLRRDeploymentContext,
):
    """
    Negative-path coverage for stamp-id endpoints when the stamp does not exist.

    1. Issue get_stamp / update_stamp / delete_stamp with a fabricated stamp_id.
    2. Verify each returns a 4xx OperationResponse.
    """
    fake_stamp_id = "STAMPS_0_99999999999999"

    # >>>>> GET MISSING STAMP >>>>>
    response, error = await lrr_client.stamp_api.get_stamp(GetStampRequest(stamp_id=fake_stamp_id))
    assert error and 400 <= error.status < 500, f"Expected 4xx for missing stamp_id, got {error}"
    del response, error
    # <<<<< GET MISSING STAMP <<<<<

    # >>>>> UPDATE MISSING STAMP >>>>>
    response, error = await lrr_client.stamp_api.update_stamp(UpdateStampRequest(
        stamp_id=fake_stamp_id, content="nope",
    ))
    assert error and 400 <= error.status < 500, f"Expected 4xx for missing stamp_id, got {error}"
    del response, error
    # <<<<< UPDATE MISSING STAMP <<<<<

    # >>>>> DELETE MISSING STAMP >>>>>
    response, error = await lrr_client.stamp_api.delete_stamp(DeleteStampRequest(stamp_id=fake_stamp_id))
    assert error and 400 <= error.status < 500, f"Expected 4xx for missing stamp_id, got {error}"
    del response, error
    # <<<<< DELETE MISSING STAMP <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("stamps")
async def test_stamp_add_missing_archive(lrr_client: LRRClient):
    """
    Pin the contract that add_stamp on a non-existent archive returns 4xx.

    1. Call add_stamp against a fabricated arcid.
    2. Assert error.status >= 400.
    """
    fake_arcid = "a" * 40
    response, error = await lrr_client.stamp_api.add_stamp(AddStampRequest(
        arcid=fake_arcid, index=0, content="should fail",
    ))
    assert error and error.status >= 400, f"Expected 4xx for missing archive, got response={response} error={error}"


@pytest.mark.asyncio
@pytest.mark.dev("stamps")
async def test_stamps_missing_archive_returns_4xx(lrr_client: LRRClient):
    """
    Pin the contract that page-scoped queries on a non-existent archive return 4xx.

    1. Call get_stamps_by_page against a fabricated arcid; assert 4xx.
    2. Call get_stamped_pages against a fabricated arcid; assert 4xx.
    """
    fake_arcid = "a" * 40

    # >>>>> GET STAMPS BY PAGE >>>>>
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(
        arcid=fake_arcid, index=0,
    ))
    assert error and 400 <= error.status < 500, f"Expected 4xx for missing archive, got response={response} error={error}"
    del response, error
    # <<<<< GET STAMPS BY PAGE <<<<<

    # >>>>> GET STAMPED PAGES >>>>>
    response, error = await lrr_client.stamp_api.get_stamped_pages(GetStampedPagesRequest(arcid=fake_arcid))
    assert error and 400 <= error.status < 500, f"Expected 4xx for missing archive, got response={response} error={error}"
    del response, error
    # <<<<< GET STAMPED PAGES <<<<<


@pytest.mark.asyncio
@pytest.mark.dev("stamps")
async def test_stamps_multi_page_invariants(
    lrr_client: LRRClient, environment: AbstractLRRDeploymentContext, semaphore: asyncio.BoundedSemaphore,
):
    """
    Multi-stamp / multi-page invariants on the rework's standalone-hash storage.

    1. Upload an archive with 3 pages.
    2. Add 3 stamps at indexes 0, 1, 2.
    3. Verify get_stamped_pages returns exactly {"0", "1", "2"}.
    4. Verify get_stamps_by_page(arcid, 1) returns exactly the page-1 stamp.
    5. Delete the page-1 stamp.
    6. Verify get_stamps_by_page(arcid, 1) is empty.
    7. Verify get_stamps_by_page(arcid, 0) and (arcid, 2) are unchanged.
    8. Verify get_stamped_pages no longer contains "1".
    """
    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "stamp_multi", 3)
        response, error = await upload_archive(lrr_client, archive_path, "stamp_multi.zip", semaphore)
        assert not error, f"Failed to upload archive (status {error.status}): {error.error}"
        arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> ADD STAMPS >>>>>
    stamp_ids = {}
    for index in (0, 1, 2):
        response, error = await lrr_client.stamp_api.add_stamp(AddStampRequest(
            arcid=arcid, index=index, content=f"stamp-{index}", position=f"{index}.0,{index}.0",
        ))
        assert not error, f"Failed to add stamp at index {index} (status {error.status}): {error.error}"
        stamp_ids[index] = response.stamp_id
    del response, error
    # <<<<< ADD STAMPS <<<<<

    # >>>>> VERIFY STAMPED PAGES >>>>>
    response, error = await lrr_client.stamp_api.get_stamped_pages(GetStampedPagesRequest(arcid=arcid))
    assert not error, f"Failed to get stamped pages (status {error.status}): {error.error}"
    assert set(response.result) == {"0", "1", "2"}, f"Expected pages {{0,1,2}}, got {response.result}"
    del response, error
    # <<<<< VERIFY STAMPED PAGES <<<<<

    # >>>>> VERIFY PAGE-1 STAMP >>>>>
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=1))
    assert not error, f"Failed to get stamps by page 1 (status {error.status}): {error.error}"
    assert len(response.result) == 1, f"Expected 1 stamp on page 1, got {len(response.result)}"
    assert response.result[0].id == stamp_ids[1]
    del response, error
    # <<<<< VERIFY PAGE-1 STAMP <<<<<

    # >>>>> DELETE PAGE-1 STAMP >>>>>
    response, error = await lrr_client.stamp_api.delete_stamp(DeleteStampRequest(stamp_id=stamp_ids[1]))
    assert not error, f"Failed to delete page-1 stamp (status {error.status}): {error.error}"
    del response, error
    # <<<<< DELETE PAGE-1 STAMP <<<<<

    # >>>>> VERIFY PAGE-1 EMPTY >>>>>
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=1))
    assert not error, f"Failed to get stamps by page 1 after delete (status {error.status}): {error.error}"
    assert len(response.result) == 0, f"Expected 0 stamps on page 1 after delete, got {len(response.result)}"
    del response, error
    # <<<<< VERIFY PAGE-1 EMPTY <<<<<

    # >>>>> VERIFY OTHER PAGES UNCHANGED >>>>>
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=0))
    assert not error, f"Failed to get stamps by page 0 (status {error.status}): {error.error}"
    assert len(response.result) == 1, f"Expected 1 stamp on page 0, got {len(response.result)}"
    assert response.result[0].id == stamp_ids[0]
    response, error = await lrr_client.stamp_api.get_stamps_by_page(GetStampsByPageRequest(arcid=arcid, index=2))
    assert not error, f"Failed to get stamps by page 2 (status {error.status}): {error.error}"
    assert len(response.result) == 1, f"Expected 1 stamp on page 2, got {len(response.result)}"
    assert response.result[0].id == stamp_ids[2]
    del response, error
    # <<<<< VERIFY OTHER PAGES UNCHANGED <<<<<

    # >>>>> VERIFY STAMPED PAGES SHRANK >>>>>
    response, error = await lrr_client.stamp_api.get_stamped_pages(GetStampedPagesRequest(arcid=arcid))
    assert not error, f"Failed to get stamped pages after delete (status {error.status}): {error.error}"
    assert set(response.result) == {"0", "2"}, f"Expected pages {{0,2}} after delete, got {response.result}"
    del response, error
    # <<<<< VERIFY STAMPED PAGES SHRANK <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)


@pytest.mark.asyncio
@pytest.mark.dev("stamps")
async def test_stamps_cleared_on_archive_delete(
    lrr_client: LRRClient, semaphore: asyncio.BoundedSemaphore,
):
    """
    Pin the contract that deleting an archive cleans up its standalone STAMPS_* keys.

    1. Upload an archive.
    2. Add a stamp; capture stamp_id.
    3. Delete the parent archive.
    4. Assert get_stamp(stamp_id) returns 4xx (orphan was cleaned up).
    """
    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "stamp_cleanup", 1)
        response, error = await upload_archive(lrr_client, archive_path, "stamp_cleanup.zip", semaphore)
        assert not error, f"Failed to upload archive (status {error.status}): {error.error}"
        arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> ADD STAMP >>>>>
    response, error = await lrr_client.stamp_api.add_stamp(AddStampRequest(
        arcid=arcid, index=0, content="orphan", position="0,0",
    ))
    assert not error, f"Failed to add stamp (status {error.status}): {error.error}"
    stamp_id = response.stamp_id
    # guard against the V1 / fresh-archive bug masquerading as a successful add (stamp_id="0")
    assert stamp_id.startswith("STAMPS_"), f"add_stamp returned non-stamp id {stamp_id!r}"
    del response, error
    # <<<<< ADD STAMP <<<<<

    # >>>>> DELETE ARCHIVE >>>>>
    response, error = await lrr_client.archive_api.delete_archive(DeleteArchiveRequest(arcid=arcid))
    assert not error, f"Failed to delete archive (status {error.status}): {error.error}"
    del response, error
    # <<<<< DELETE ARCHIVE <<<<<

    # >>>>> VERIFY ORPHAN CLEANED >>>>>
    response, error = await lrr_client.stamp_api.get_stamp(GetStampRequest(stamp_id=stamp_id))
    assert error and 400 <= error.status < 500, (
        f"Expected 4xx for orphaned stamp after archive delete, got response={response} error={error}"
    )
    # <<<<< VERIFY ORPHAN CLEANED <<<<<


@pytest.mark.asyncio
@pytest.mark.dev("stamps")
async def test_stamps_require_api_key(
    lrr_client: LRRClient, environment: AbstractLRRDeploymentContext, semaphore: asyncio.BoundedSemaphore,
):
    """
    Verify add_stamp / update_stamp / delete_stamp require an API key.

    1. Upload an archive with the authed client.
    2. Construct an unauthed client (lrr_api_key=None).
    3. Attempt add_stamp / update_stamp / delete_stamp; assert 401 or 403 on each.
    """
    # >>>>> UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = create_archive_file(Path(tmpdir), "stamp_auth", 1)
        response, error = await upload_archive(lrr_client, archive_path, "stamp_auth.zip", semaphore)
        assert not error, f"Failed to upload archive (status {error.status}): {error.error}"
        arcid = response.arcid
    del response, error
    # <<<<< UPLOAD STAGE <<<<<

    # >>>>> UNAUTHED CALLS >>>>>
    async with LRRClient(lrr_client.lrr_base_url, lrr_api_key=None) as unauthed:
        response, error = await unauthed.stamp_api.add_stamp(AddStampRequest(
            arcid=arcid, index=0, content="nope", position="0,0",
        ))
        assert error and error.status in (401, 403), f"Expected 401/403 for unauthed add_stamp, got response={response} error={error}"
        del response, error

        response, error = await unauthed.stamp_api.update_stamp(UpdateStampRequest(
            stamp_id="STAMPS_0_0", content="nope",
        ))
        assert error and error.status in (401, 403), f"Expected 401/403 for unauthed update_stamp, got response={response} error={error}"
        del response, error

        response, error = await unauthed.stamp_api.delete_stamp(DeleteStampRequest(stamp_id="STAMPS_0_0"))
        assert error and error.status in (401, 403), f"Expected 401/403 for unauthed delete_stamp, got response={response} error={error}"
        del response, error
    # <<<<< UNAUTHED CALLS <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)
