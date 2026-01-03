"""
Search result handling integration tests for LANraragi.

These tests cover search result handling including:
- Pagination (start=0, start=N, start=-1, edge cases)
- Random archive search
- Custom namespace sorting (sortby=artist, sortby=parody)

Due to the concurrent nature of these tests,
a flake-catching xfail test is included at the start to prime Windows test environments.
"""

import asyncio
import logging
from pathlib import Path
import pytest
import sys
import tempfile
from typing import Dict, Set

from lanraragi.clients.client import LRRClient
from lanraragi.models.search import GetRandomArchivesRequest, SearchArchiveIndexRequest

from aio_lanraragi_tests.common import compute_archive_id
from aio_lanraragi_tests.helpers import (
    create_archive_file,
    expect_no_error_logs,
    xfail_catch_flakes_inner,
    upload_archive,
)
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext

LOGGER = logging.getLogger(__name__)


@pytest.mark.skipif(sys.platform != "win32", reason="Cache priming required only for flaky Windows testing environments.")
@pytest.mark.asyncio
@pytest.mark.xfail
async def test_xfail_catch_flakes(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    This xfail test case serves no integration testing purpose, other than to prime the cache of flaky testing hosts
    and reduce the chances of subsequent test case failures caused by network flakes.
    """
    await xfail_catch_flakes_inner(lrr_client, semaphore, environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_search_pagination(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Pagination functionality tests covering:
    - start=0 (first page)
    - start=N (offset pagination)
    - start=-1 (return all results, no paging)
    - Edge case: start beyond result count
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")

    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    assert not any(environment.archives_dir.iterdir()), "Archive directory is not empty!"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> ARCHIVE DEFINITION >>>>>
    # Create 15 archives to test pagination (default page size is 100, so we'll use smaller numbers)
    archive_specs = [
        {"name": f"pagination_test_{i:02d}", "title": f"Pagination Test Archive {i:02d}", "tags": f"series:test,index:{i}", "pages": 10 + i}
        for i in range(1, 16)
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for pagination testing.")

        for spec in archive_specs:
            save_path = create_archive_file(tmpdir, spec["name"], spec["pages"])
            arcid = compute_archive_id(save_path)

            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=spec["title"], tags=spec["tags"]
            )
            assert not error, f"Failed to upload {spec['title']} (status {error.status}): {error.error}"
            assert response.arcid == arcid, f"Archive ID mismatch for {spec['title']}"
            title_to_arcid[spec["title"]] = arcid
            LOGGER.debug(f"Uploaded {spec['title']} with arcid {arcid}")

    LOGGER.debug(f"Uploaded {len(title_to_arcid)} archives.")
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # >>>>> PAGINATION TESTS: START=0 (FIRST PAGE) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="0", sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"Pagination start=0 failed (status {error.status}): {error.error}"
    assert response.records_total == 15, f"Expected 15 total records, got {response.records_total}"
    assert response.records_filtered == 15, f"Expected 15 filtered records, got {response.records_filtered}"
    # Default page size is 100, so all 15 should be returned
    assert len(response.data) == 15, f"Expected 15 records on first page, got {len(response.data)}"
    # Verify sort order
    titles = [r.title for r in response.data]
    assert titles == sorted(titles), f"Results not sorted correctly: {titles}"
    LOGGER.debug("Pagination start=0 test passed.")
    # <<<<< PAGINATION TESTS: START=0 (FIRST PAGE) <<<<<

    # >>>>> PAGINATION TESTS: START=5 (OFFSET) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="5", sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"Pagination start=5 failed (status {error.status}): {error.error}"
    assert response.records_filtered == 15, f"Expected 15 filtered records, got {response.records_filtered}"
    # Should return archives 6-15 (10 archives, since we skip the first 5)
    assert len(response.data) == 10, f"Expected 10 records after offset 5, got {len(response.data)}"
    # First result should be "Pagination Test Archive 06"
    assert response.data[0].title == "Pagination Test Archive 06", f"Expected first result to be Archive 06, got {response.data[0].title}"
    LOGGER.debug("Pagination start=5 test passed.")
    # <<<<< PAGINATION TESTS: START=5 (OFFSET) <<<<<

    # >>>>> PAGINATION TESTS: START=10 (HIGHER OFFSET) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="10", sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"Pagination start=10 failed (status {error.status}): {error.error}"
    # Should return archives 11-15 (5 archives)
    assert len(response.data) == 5, f"Expected 5 records after offset 10, got {len(response.data)}"
    assert response.data[0].title == "Pagination Test Archive 11", f"Expected first result to be Archive 11, got {response.data[0].title}"
    LOGGER.debug("Pagination start=10 test passed.")
    # <<<<< PAGINATION TESTS: START=10 (HIGHER OFFSET) <<<<<

    # >>>>> PAGINATION TESTS: START=-1 (ALL RESULTS) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="-1", sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"Pagination start=-1 failed (status {error.status}): {error.error}"
    # start=-1 should return all results without pagination
    assert len(response.data) == 15, f"Expected all 15 records with start=-1, got {len(response.data)}"
    assert response.records_filtered == 15, f"Expected 15 filtered records, got {response.records_filtered}"
    LOGGER.debug("Pagination start=-1 (all results) test passed.")
    # <<<<< PAGINATION TESTS: START=-1 (ALL RESULTS) <<<<<

    # >>>>> PAGINATION TESTS: START BEYOND RESULT COUNT >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="100", sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"Pagination start=100 failed (status {error.status}): {error.error}"
    # Start beyond result count should return empty data
    assert len(response.data) == 0, f"Expected 0 records when start exceeds count, got {len(response.data)}"
    assert response.records_filtered == 15, f"Expected 15 filtered records, got {response.records_filtered}"
    LOGGER.debug("Pagination start beyond result count test passed.")
    # <<<<< PAGINATION TESTS: START BEYOND RESULT COUNT <<<<<

    # >>>>> PAGINATION TESTS: START=14 (LAST ITEM) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(start="14", sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"Pagination start=14 failed (status {error.status}): {error.error}"
    # Should return only the last archive
    assert len(response.data) == 1, f"Expected 1 record at offset 14, got {len(response.data)}"
    assert response.data[0].title == "Pagination Test Archive 15", f"Expected last archive, got {response.data[0].title}"
    LOGGER.debug("Pagination start=14 (last item) test passed.")
    # <<<<< PAGINATION TESTS: START=14 (LAST ITEM) <<<<<

    # >>>>> PAGINATION TESTS: WITH FILTER >>>>>
    # Test pagination with a search filter that returns fewer results
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(
            search_filter="Pagination Test Archive 1",
            start="0", sortby="title", order="asc", groupby_tanks=False
        )
    )
    assert not error, f"Pagination with filter failed (status {error.status}): {error.error}"
    # Should match archives 10-15 (6 archives).
    assert response.records_filtered == 6, f"Expected 6 filtered records, got {response.records_filtered}"
    LOGGER.debug("Pagination with filter test passed.")
    # <<<<< PAGINATION TESTS: WITH FILTER <<<<<

    # no error logs
    expect_no_error_logs(environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_random_archive_search(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Random archive search functionality tests covering:
    - Random archive endpoint with default count
    - Random with custom count
    - Random with filters applied
    - Verifying randomness (multiple calls may return different results)
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")

    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    assert not any(environment.archives_dir.iterdir()), "Archive directory is not empty!"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> ARCHIVE DEFINITION >>>>>
    archive_specs = [
        {"name": "archive_01", "title": "Title 01", "tags": "group:a,series:test", "pages": 10},
        {"name": "archive_02", "title": "Title 02", "tags": "group:b,series:test", "pages": 15},
        {"name": "archive_03", "title": "Title 03", "tags": "group:a,series:test", "pages": 20},
        {"name": "archive_04", "title": "Title 04", "tags": "group:b,series:test", "pages": 25},
        {"name": "archive_05", "title": "Title 05", "tags": "group:a,series:test", "pages": 30},
        {"name": "archive_06", "title": "Title 06", "tags": "group:c,series:test", "pages": 35},
        {"name": "archive_07", "title": "Title 07", "tags": "group:c,series:test", "pages": 40},
        {"name": "archive_08", "title": "Title 08", "tags": "group:d,series:test", "pages": 45},
        {"name": "archive_09", "title": "Title 09", "tags": "group:d,series:test", "pages": 50},
        {"name": "archive_10", "title": "Title 10", "tags": "group:e,series:test", "pages": 55},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for random search testing.")

        for spec in archive_specs:
            save_path = create_archive_file(tmpdir, spec["name"], spec["pages"])
            arcid = compute_archive_id(save_path)

            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=spec["title"], tags=spec["tags"]
            )
            assert not error, f"Failed to upload {spec['title']} (status {error.status}): {error.error}"
            assert response.arcid == arcid, f"Archive ID mismatch for {spec['title']}"
            title_to_arcid[spec["title"]] = arcid
            LOGGER.debug(f"Uploaded {spec['title']} with arcid {arcid}")

    LOGGER.debug(f"Uploaded {len(title_to_arcid)} archives.")
    all_arcids = set(title_to_arcid.values())
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # Helper function for assertions
    def get_result_arcids(response) -> Set[str]:
        return {r.arcid for r in response.data}

    # >>>>> RANDOM TESTS: DEFAULT COUNT >>>>>
    response, error = await lrr_client.search_api.get_random_archives(
        GetRandomArchivesRequest(groupby_tanks=False)
    )
    assert not error, f"Random search with default count failed (status {error.status}): {error.error}"
    # Default count is 5
    assert len(response.data) == 5, f"Expected 5 random archives (default), got {len(response.data)}"
    # All returned arcids should be valid
    returned_arcids = get_result_arcids(response)
    assert returned_arcids.issubset(all_arcids), f"Returned arcids {returned_arcids} not subset of {all_arcids}"
    LOGGER.debug("Random search with default count test passed.")
    # <<<<< RANDOM TESTS: DEFAULT COUNT <<<<<

    # >>>>> RANDOM TESTS: CUSTOM COUNT >>>>>
    response, error = await lrr_client.search_api.get_random_archives(
        GetRandomArchivesRequest(count=3, groupby_tanks=False)
    )
    assert not error, f"Random search with count=3 failed (status {error.status}): {error.error}"
    assert len(response.data) == 3, f"Expected 3 random archives, got {len(response.data)}"
    LOGGER.debug("Random search with count=3 test passed.")
    # <<<<< RANDOM TESTS: CUSTOM COUNT <<<<<

    # >>>>> RANDOM TESTS: COUNT LARGER THAN AVAILABLE >>>>>
    response, error = await lrr_client.search_api.get_random_archives(
        GetRandomArchivesRequest(count=100, groupby_tanks=False)
    )
    assert not error, f"Random search with count=100 failed (status {error.status}): {error.error}"
    # Should return all 10 archives (capped at available count)
    assert len(response.data) == 10, f"Expected 10 random archives (all available), got {len(response.data)}"
    LOGGER.debug("Random search with count exceeding available test passed.")
    # <<<<< RANDOM TESTS: COUNT LARGER THAN AVAILABLE <<<<<

    # >>>>> RANDOM TESTS: WITH FILTER >>>>>
    # Filter to only group:a archives (Title 01, 03, 05 = 3 archives)
    response, error = await lrr_client.search_api.get_random_archives(
        GetRandomArchivesRequest(filter="group:a", count=10, groupby_tanks=False)
    )
    assert not error, f"Random search with filter failed (status {error.status}): {error.error}"
    assert len(response.data) == 3, f"Expected 3 archives matching filter, got {len(response.data)}"
    expected_titles = {"Title 01", "Title 03", "Title 05"}
    returned_titles = {r.title for r in response.data}
    assert returned_titles == expected_titles, f"Filter mismatch: got {returned_titles}, expected {expected_titles}"
    LOGGER.debug("Random search with filter test passed.")
    # <<<<< RANDOM TESTS: WITH FILTER <<<<<

    # >>>>> RANDOM TESTS: COUNT=1 >>>>>
    response, error = await lrr_client.search_api.get_random_archives(
        GetRandomArchivesRequest(count=1, groupby_tanks=False)
    )
    assert not error, f"Random search with count=1 failed (status {error.status}): {error.error}"
    assert len(response.data) == 1, f"Expected 1 random archive, got {len(response.data)}"
    LOGGER.debug("Random search with count=1 test passed.")
    # <<<<< RANDOM TESTS: COUNT=1 <<<<<

    # >>>>> RANDOM TESTS: VERIFY RANDOMNESS >>>>>
    # Make multiple calls and check that we get different results at least sometimes
    # Note: This test may rarely fail due to randomness, but with 10 archives and 3 selections,
    # the probability of getting the same 3 in the same order twice is very low
    results_set = set()
    for i in range(5):
        response, error = await lrr_client.search_api.get_random_archives(
            GetRandomArchivesRequest(count=3, groupby_tanks=False)
        )
        assert not error, f"Random search iteration {i} failed: {error.error}"
        # Create a frozenset of arcids for this result
        result_arcids = frozenset(r.arcid for r in response.data)
        results_set.add(result_arcids)

    # We expect at least 2 different results out of 5 calls (very likely with 10 archives)
    # This is a soft check - randomness could theoretically give same results
    if len(results_set) == 1:
        LOGGER.warning("All 5 random calls returned the same set - possible but unlikely")
    else:
        LOGGER.debug(f"Random search produced {len(results_set)} different result sets out of 5 calls.")
    # <<<<< RANDOM TESTS: VERIFY RANDOMNESS <<<<<

    # >>>>> RANDOM TESTS: FILTER WITH NO MATCHES >>>>>
    response, error = await lrr_client.search_api.get_random_archives(
        GetRandomArchivesRequest(filter="nonexistent_tag_xyz", count=5, groupby_tanks=False)
    )
    assert not error, f"Random search with no-match filter failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Expected 0 archives for non-matching filter, got {len(response.data)}"
    LOGGER.debug("Random search with no-match filter test passed.")
    # <<<<< RANDOM TESTS: FILTER WITH NO MATCHES <<<<<

    # no error logs
    expect_no_error_logs(environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_custom_namespaces(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Custom namespace sort functionality tests covering:
    - Sort by artist namespace (sortby=artist)
    - Sort by custom namespace (sortby=parody)
    - Archives without the namespace value should sort to end
    - Ascending and descending order
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"
    LOGGER.debug("Established connection with test LRR server.")

    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    assert not any(environment.archives_dir.iterdir()), "Archive directory is not empty!"
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> ARCHIVE DEFINITION >>>>>
    # Archives with varying artist and parody tags to test namespace sorting
    # artist values: a, a, b, c, d (and 2 without artist)
    # parody values: w, x, y, z, v (and 2 without parody)
    archive_specs = [
        {"name": "archive_1", "title": "Title 1", "tags": "artist:c,parody:z", "pages": 10},
        {"name": "archive_2", "title": "Title 2", "tags": "artist:a,parody:x", "pages": 15},
        {"name": "archive_3", "title": "Title 3", "tags": "artist:b,parody:y", "pages": 20},
        {"name": "archive_4", "title": "Title 4 No Artist", "tags": "parody:w", "pages": 25},  # No artist tag
        {"name": "archive_5", "title": "Title 5 No Parody", "tags": "artist:d", "pages": 30},  # No parody tag
        {"name": "archive_6", "title": "Title 6 No Tags", "tags": "", "pages": 35},  # No tags at all
        {"name": "archive_7", "title": "Title 7", "tags": "artist:a,parody:v", "pages": 40},  # Duplicate artist (a)
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for namespace sort testing.")

        for spec in archive_specs:
            save_path = create_archive_file(tmpdir, spec["name"], spec["pages"])
            arcid = compute_archive_id(save_path)

            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=spec["title"], tags=spec["tags"] if spec["tags"] else None
            )
            assert not error, f"Failed to upload {spec['title']} (status {error.status}): {error.error}"
            assert response.arcid == arcid, f"Archive ID mismatch for {spec['title']}"
            title_to_arcid[spec["title"]] = arcid
            LOGGER.debug(f"Uploaded {spec['title']} with arcid {arcid}")

    LOGGER.debug(f"Uploaded {len(title_to_arcid)} archives.")
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # >>>>> NAMESPACE SORT TESTS: ARTIST ASCENDING >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="artist", order="asc", groupby_tanks=False)
    )
    assert not error, f"Sort by artist asc failed (status {error.status}): {error.error}"
    titles = [r.title for r in response.data]
    # Expected order: artist:a (x2), artist:b, artist:c, artist:d, then archives without artist (sort to end with "zzzz")
    # Archives without artist: "Title 4 No Artist", "Title 6 No Tags"
    # The two without artist tags should be at the end
    archives_with_artist = titles[:-2]
    archives_without_artist = titles[-2:]

    # Verify archives with artist:a are sorted first (Title 2 and Title 7 both have artist:a)
    assert "Title 2" in archives_with_artist[:2], f"artist:a archives should be first: {archives_with_artist}"
    assert "Title 7" in archives_with_artist[:2], f"artist:a archives should be first: {archives_with_artist}"

    # Archives without artist should be at the end
    assert set(archives_without_artist) == {"Title 4 No Artist", "Title 6 No Tags"}, \
        f"Archives without artist should be at end: {archives_without_artist}"
    LOGGER.debug("Sort by artist ascending test passed.")
    # <<<<< NAMESPACE SORT TESTS: ARTIST ASCENDING <<<<<

    # >>>>> NAMESPACE SORT TESTS: ARTIST DESCENDING >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="artist", order="desc", groupby_tanks=False)
    )
    assert not error, f"Sort by artist desc failed (status {error.status}): {error.error}"
    titles = [r.title for r in response.data]
    # In descending order, archives without artist (zzzz) are reversed to the front
    # Order: zzzz (x2), d, c, b, a, a
    # First two should be the ones without artist tag
    archives_without_artist = titles[:2]
    assert set(archives_without_artist) == {"Title 4 No Artist", "Title 6 No Tags"}, \
        f"Archives without artist should be first in desc order: {archives_without_artist}"
    LOGGER.debug("Sort by artist descending test passed.")
    # <<<<< NAMESPACE SORT TESTS: ARTIST DESCENDING <<<<<

    # >>>>> NAMESPACE SORT TESTS: PARODY ASCENDING >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="parody", order="asc", groupby_tanks=False)
    )
    assert not error, f"Sort by parody asc failed (status {error.status}): {error.error}"
    titles = [r.title for r in response.data]
    # Expected order by parody: v, w, x, y, z, then without parody (zzzz)
    # Archives without parody: "Title 5 No Parody", "Title 6 No Tags"
    archives_without_parody = titles[-2:]
    assert set(archives_without_parody) == {"Title 5 No Parody", "Title 6 No Tags"}, \
        f"Archives without parody should be at end: {archives_without_parody}"
    LOGGER.debug("Sort by parody ascending test passed.")
    # <<<<< NAMESPACE SORT TESTS: PARODY ASCENDING <<<<<

    # >>>>> NAMESPACE SORT TESTS: NON-EXISTENT NAMESPACE >>>>>
    # Sorting by a namespace that no archive has should put all at end (zzzz)
    # All archives should still be returned
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="nonexistent_ns", order="asc", groupby_tanks=False)
    )
    assert not error, f"Sort by nonexistent namespace failed (status {error.status}): {error.error}"
    assert len(response.data) == 7, f"Expected 7 archives, got {len(response.data)}"
    LOGGER.debug("Sort by nonexistent namespace test passed.")
    # <<<<< NAMESPACE SORT TESTS: NON-EXISTENT NAMESPACE <<<<<

    # no error logs
    expect_no_error_logs(environment)
