"""
Test that archives missing a custom sort key namespace always appear at the
back, regardless of sort direction or cache state.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.search import SearchArchiveIndexRequest

from aio_lanraragi_tests.common import compute_archive_id
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    trigger_stat_rebuild,
    upload_archive,
)

LOGGER = logging.getLogger(__name__)


@pytest.mark.asyncio
# @pytest.mark.xfail(reason="PR: https://github.com/Difegue/LANraragi/pull/1473", strict=False)
# xfail disabled: PgSearch partitions keyed/unkeyed archives correctly via SQL CASE expression.
# When upstream PR #1473 merges, remove this comment and the commented-out xfail line.
async def test_custom_sort_unkeyed_order(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext,
):
    """
    Test that archives missing the sort namespace appear at the back,
    and that keyed archives are correctly ordered by sort direction.

    1. Upload 6 archives: 4 with source namespace, 2 without (but with other tags).
    2. For each sort direction (asc, desc):
       a. Clear cache, search sorted by source -- verify keyed order and unkeyed at back.
       b. Repeat same search -- verify idempotent (cached path), count, and order.
    3. Clear cache, search asc, then search desc -- verify order and unkeyed at back
       (reversed-from-cache path).
    4. Clear cache, search desc, then search asc -- same verification.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")

    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    # archives 1-4 have source: namespace, archives 5-6 have tags but no source:
    archive_specs = [
        {"name": "archive_1", "title": "Title 1", "tags": "source:d,artist:a", "pages": 3},
        {"name": "archive_2", "title": "Title 2", "tags": "source:b,artist:b", "pages": 3},
        {"name": "archive_3", "title": "Title 3", "tags": "source:c,artist:c", "pages": 3},
        {"name": "archive_4", "title": "Title 4", "tags": "source:a,artist:d", "pages": 3},
        {"name": "archive_5", "title": "Title 5", "tags": "artist:e,series:a", "pages": 3},
        {"name": "archive_6", "title": "Title 6", "tags": "artist:f,group:a", "pages": 3},
    ]

    unkeyed_titles = {"Title 5", "Title 6"}
    # source:a=Title 4, source:b=Title 2, source:c=Title 3, source:d=Title 1
    keyed_asc = ["Title 4", "Title 2", "Title 3", "Title 1"]
    keyed_desc = ["Title 1", "Title 3", "Title 2", "Title 4"]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        for spec in archive_specs:
            save_path = create_archive_file(tmpdir, spec["name"], spec["pages"])
            arcid = compute_archive_id(save_path)
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=spec["title"], tags=spec["tags"],
            )
            assert not error, f"Upload failed for {spec['title']} (status {error.status}): {error.error}"
            assert response.arcid == arcid, f"Archive ID mismatch for {spec['title']}"

    LOGGER.debug(f"Uploaded {len(archive_specs)} archives.")
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # >>>>> STAT REBUILD STAGE >>>>>
    await trigger_stat_rebuild(lrr_client)
    LOGGER.debug("Stat hash rebuild completed.")
    # <<<<< STAT REBUILD STAGE <<<<<

    # >>>>> SORT TESTS: FRESH ASC >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"

    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="source", order="asc", groupby_tanks=False)
    )
    assert not error, f"Sort by source asc failed (status {error.status}): {error.error}"
    assert len(response.data) == 6, f"Expected 6 results, got {len(response.data)}"
    titles = [r.title for r in response.data]
    assert titles[:4] == keyed_asc, f"Keyed archives not in ascending order (asc fresh): {titles}"
    assert set(titles[4:]) == unkeyed_titles, f"Unkeyed archives not at back (asc fresh): {titles}"
    LOGGER.debug(f"Fresh asc order: {titles}")
    # <<<<< SORT TESTS: FRESH ASC <<<<<

    # >>>>> SORT TESTS: CACHED ASC (idempotent) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="source", order="asc", groupby_tanks=False)
    )
    assert not error, f"Cached sort by source asc failed (status {error.status}): {error.error}"
    assert response.records_filtered == 6, f"Expected 6 filtered, got {response.records_filtered} (asc cached)"
    titles = [r.title for r in response.data]
    assert titles[:4] == keyed_asc, f"Keyed archives not in ascending order (asc cached): {titles}"
    assert set(titles[4:]) == unkeyed_titles, f"Unkeyed archives not at back (asc cached): {titles}"
    LOGGER.debug(f"Cached asc order: {titles}")
    # <<<<< SORT TESTS: CACHED ASC (idempotent) <<<<<

    # >>>>> SORT TESTS: FRESH DESC >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"

    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="source", order="desc", groupby_tanks=False)
    )
    assert not error, f"Sort by source desc failed (status {error.status}): {error.error}"
    assert len(response.data) == 6, f"Expected 6 results, got {len(response.data)}"
    titles = [r.title for r in response.data]
    assert titles[:4] == keyed_desc, f"Keyed archives not in descending order (desc fresh): {titles}"
    assert set(titles[4:]) == unkeyed_titles, f"Unkeyed archives not at back (desc fresh): {titles}"
    LOGGER.debug(f"Fresh desc order: {titles}")
    # <<<<< SORT TESTS: FRESH DESC <<<<<

    # >>>>> SORT TESTS: CACHED DESC (idempotent) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="source", order="desc", groupby_tanks=False)
    )
    assert not error, f"Cached sort by source desc failed (status {error.status}): {error.error}"
    assert response.records_filtered == 6, f"Expected 6 filtered, got {response.records_filtered} (desc cached)"
    titles = [r.title for r in response.data]
    assert titles[:4] == keyed_desc, f"Keyed archives not in descending order (desc cached): {titles}"
    assert set(titles[4:]) == unkeyed_titles, f"Unkeyed archives not at back (desc cached): {titles}"
    LOGGER.debug(f"Cached desc order: {titles}")
    # <<<<< SORT TESTS: CACHED DESC (idempotent) <<<<<

    # >>>>> SORT TESTS: REVERSED-FROM-CACHE (asc then desc) >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"

    # prime cache with asc
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="source", order="asc", groupby_tanks=False)
    )
    assert not error, f"Cache prime asc failed (status {error.status}): {error.error}"

    # query desc (hits reversed-from-cache path)
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="source", order="desc", groupby_tanks=False)
    )
    assert not error, f"Reversed-from-cache desc failed (status {error.status}): {error.error}"
    assert response.records_filtered == 6, f"Expected 6 filtered, got {response.records_filtered} (asc->desc cache reverse)"
    titles = [r.title for r in response.data]
    assert titles[:4] == keyed_desc, f"Keyed archives not in descending order (asc->desc cache reverse): {titles}"
    assert set(titles[4:]) == unkeyed_titles, f"Unkeyed archives not at back (asc->desc cache reverse): {titles}"
    LOGGER.debug(f"Reversed-from-cache (asc->desc) order: {titles}")
    # <<<<< SORT TESTS: REVERSED-FROM-CACHE (asc then desc) <<<<<

    # >>>>> SORT TESTS: REVERSED-FROM-CACHE (desc then asc) >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"

    # prime cache with desc
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="source", order="desc", groupby_tanks=False)
    )
    assert not error, f"Cache prime desc failed (status {error.status}): {error.error}"

    # query asc (hits reversed-from-cache path)
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="source", order="asc", groupby_tanks=False)
    )
    assert not error, f"Reversed-from-cache asc failed (status {error.status}): {error.error}"
    assert response.records_filtered == 6, f"Expected 6 filtered, got {response.records_filtered} (desc->asc cache reverse)"
    titles = [r.title for r in response.data]
    assert titles[:4] == keyed_asc, f"Keyed archives not in ascending order (desc->asc cache reverse): {titles}"
    assert set(titles[4:]) == unkeyed_titles, f"Unkeyed archives not at back (desc->asc cache reverse): {titles}"
    LOGGER.debug(f"Reversed-from-cache (desc->asc) order: {titles}")
    # <<<<< SORT TESTS: REVERSED-FROM-CACHE (desc then asc) <<<<<

    # >>>>> DISCARD SEARCH CACHE >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"
    # <<<<< DISCARD SEARCH CACHE <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)
