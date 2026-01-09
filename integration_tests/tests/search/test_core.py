"""
Core search API integration tests for LANraragi.

These tests cover the fundamental search functionality including:
- Text/title search
- Namespace filtering (exact, fuzzy, very fuzzy)
- Wildcard searches
- Tag inclusion/exclusion
- Category filtering (static and dynamic)
- New/untagged filters
- Pagecount and read count filters
- Sort options
- Tankoubon grouping

Due to the concurrent nature of these tests,
a flake-catching xfail test is included at the start to prime Windows test environments.
"""

import asyncio
import logging
from pathlib import Path
import pytest
import sys
import tempfile
from typing import Dict

from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import (
    ClearNewArchiveFlagRequest,
    UpdateReadingProgressionRequest,
)
from lanraragi.models.category import (
    AddArchiveToCategoryRequest,
    CreateCategoryRequest,
)
from lanraragi.models.search import SearchArchiveIndexRequest
from lanraragi.models.tankoubon import (
    AddArchiveToTankoubonRequest,
    CreateTankoubonRequest,
)

from aio_lanraragi_tests.common import compute_archive_id
from aio_lanraragi_tests.helpers import (
    create_archive_file,
    expect_no_error_logs,
    retry_on_lock,
    trigger_stat_rebuild,
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
async def test_search_functionality(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Comprehensive search API functionality test covering:
    - Text/title search
    - Namespace filtering (exact, fuzzy, very fuzzy)
    - Wildcard searches
    - Tag inclusion/exclusion
    - Category filtering (static and dynamic)
    - New/untagged filters
    - Pagecount and read count filters
    - Sort options
    - Tankoubon grouping
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
        {"name": "fate_go_memo", "title": "Fate GO MEMO", "tags": "artist:wada rco,character:ereshkigal,male:very cool", "pages": 50},
        {"name": "fate_go_memo_2", "title": "Fate GO MEMO 2", "tags": "artist:wada rco,character:waver velvet", "pages": 30},
        {"name": "ghost_in_the_shell", "title": "Ghost in the Shell", "tags": "artist:shirow masamune,full color,artbook", "pages": 200},
        {"name": "saturn_japanese", "title": "Saturn Backup Cartridge - Japanese Manual", "tags": "character:segata,male:cool", "pages": 160},
        {"name": "saturn_american", "title": "Saturn Backup Cartridge - American Manual", "tags": "character:segata,female:very cool", "pages": 180},
        {"name": "medjed_collection", "title": "Medjed Collection", "tags": "vector,medjed", "pages": 20},
        {"name": "vector_art_book", "title": "Vector Art Book", "tags": "vector", "pages": 40},
        {"name": "new_release", "title": "New Release Archive", "tags": "new_release_tag", "pages": 10},
        {"name": "untagged", "title": "Untagged Archive", "tags": "", "pages": 5},
        {"name": "cool_guy", "title": "Cool Guy Adventures", "tags": "male:cool", "pages": 25},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives with specific page counts.")

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

    # >>>>> SETUP: STATIC CATEGORY >>>>>
    response, error = await lrr_client.category_api.create_category(
        CreateCategoryRequest(name="Segata Sanshiro")
    )
    assert not error, f"Failed to create static category (status {error.status}): {error.error}"
    static_category_id = response.category_id

    for title in ["Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"]:
        response, error = await retry_on_lock(lambda t=title: lrr_client.category_api.add_archive_to_category(
            AddArchiveToCategoryRequest(category_id=static_category_id, arcid=title_to_arcid[t])
        ))
        assert not error, f"Failed to add {title} to category (status {error.status}): {error.error}"
    LOGGER.debug("Created static category 'Segata Sanshiro' with 2 archives.")
    # <<<<< SETUP: STATIC CATEGORY <<<<<

    # >>>>> SETUP: DYNAMIC CATEGORY >>>>>
    response, error = await lrr_client.category_api.create_category(
        CreateCategoryRequest(name="American Filter", search="American")
    )
    assert not error, f"Failed to create dynamic category (status {error.status}): {error.error}"
    dynamic_category_id = response.category_id
    LOGGER.debug("Created dynamic category 'American Filter'.")
    # <<<<< SETUP: DYNAMIC CATEGORY <<<<<

    # >>>>> SETUP: TANKOUBON >>>>>
    response, error = await lrr_client.tankoubon_api.create_tankoubon(
        CreateTankoubonRequest(name="Vector Series")
    )
    assert not error, f"Failed to create tankoubon (status {error.status}): {error.error}"
    tankoubon_id = response.tank_id

    for title in ["Medjed Collection", "Vector Art Book"]:
        response, error = await lrr_client.tankoubon_api.add_archive_to_tankoubon(
            AddArchiveToTankoubonRequest(tank_id=tankoubon_id, arcid=title_to_arcid[title])
        )
        assert not error, f"Failed to add {title} to tankoubon (status {error.status}): {error.error}"
    LOGGER.debug("Created tankoubon 'Vector Series' with 2 archives.")
    # <<<<< SETUP: TANKOUBON <<<<<

    # >>>>> SETUP: SIMULATE READS >>>>>
    read_specs = [
        ("Ghost in the Shell", 10),
        ("Saturn Backup Cartridge - Japanese Manual", 10),
        ("Saturn Backup Cartridge - American Manual", 5),
    ]
    for title, read_count in read_specs:
        arcid = title_to_arcid[title]
        for page in range(1, read_count + 1):
            response, error = await retry_on_lock(lambda a=arcid, p=page: lrr_client.archive_api.update_reading_progression(
                UpdateReadingProgressionRequest(arcid=a, page=p)
            ))
            assert not error, f"Failed to update progress for {title} (status {error.status}): {error.error}"
    LOGGER.debug("Simulated reading progress for 3 archives.")
    # <<<<< SETUP: SIMULATE READS <<<<<

    # >>>>> SETUP: CLEAR NEW FLAGS >>>>>
    # Keep only "New Release Archive" as new
    for title, arcid in title_to_arcid.items():
        if title == "New Release Archive":
            continue
        response, error = await retry_on_lock(lambda a=arcid: lrr_client.archive_api.clear_new_archive_flag(
            ClearNewArchiveFlagRequest(arcid=a)
        ))
        assert not error, f"Failed to clear new flag for {title} (status {error.status}): {error.error}"
    LOGGER.debug("Cleared new flags for 9 archives, kept 1 as new.")
    # <<<<< SETUP: CLEAR NEW FLAGS <<<<<


    # >>>>> SEARCH TESTS: EMPTY SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(groupby_tanks=False)
    )
    assert not error, f"Empty search failed (status {error.status}): {error.error}"
    assert len(response.data) == 10, f"Empty search should return 10 archives, got {len(response.data)}"
    # <<<<< SEARCH TESTS: EMPTY SEARCH <<<<<

    # >>>>> SEARCH TESTS: BASIC TITLE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Ghost in the Shell", groupby_tanks=False)
    )
    assert not error, f"Title search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Ghost in the Shell"}, f"Title search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: BASIC TITLE SEARCH <<<<<

    # >>>>> SEARCH TESTS: EXACT NAMESPACE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"male:very cool"', groupby_tanks=False)
    )
    assert not error, f"Exact namespace search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Fate GO MEMO"}, f"Exact namespace search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: EXACT NAMESPACE SEARCH <<<<<

    # >>>>> SEARCH TESTS: FUZZY NAMESPACE SEARCH >>>>>
    # Fuzzy search: namespace must match exactly, value fuzzy-matches
    # "male:very cool" matches archives with namespace "male:" where value contains "very cool"
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="male:very cool", groupby_tanks=False)
    )
    assert not error, f"Fuzzy namespace search failed (status {error.status}): {error.error}"
    expected = {"Fate GO MEMO"}  # Only archive with "male:very cool" tag
    assert {r.title for r in response.data} == expected, f"Fuzzy namespace search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: FUZZY NAMESPACE SEARCH <<<<<

    # >>>>> SEARCH TESTS: VERY FUZZY NAMESPACE SEARCH >>>>>
    # Very fuzzy (*prefix): namespace can have prefix (e.g., "female" matches "*male")
    # "*male:very cool" matches namespaces ending with "male" (male, female) where value contains "very cool"
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="*male:very cool", groupby_tanks=False)
    )
    assert not error, f"Very fuzzy namespace search failed (status {error.status}): {error.error}"
    expected = {"Fate GO MEMO", "Saturn Backup Cartridge - American Manual"}  # male:very cool + female:very cool
    assert {r.title for r in response.data} == expected, f"Very fuzzy namespace search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: VERY FUZZY NAMESPACE SEARCH <<<<<

    # >>>>> SEARCH TESTS: WILDCARD ? SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Fate GO MEMO ?"', groupby_tanks=False)
    )
    assert not error, f"Wildcard ? search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Fate GO MEMO 2"}, f"Wildcard ? search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: WILDCARD ? SEARCH <<<<<

    # >>>>> SEARCH TESTS: WILDCARD * SEARCH >>>>>
    # Wildcard * in quotes matches full title pattern
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Saturn*Cartridge*Japanese Manual"', groupby_tanks=False)
    )
    assert not error, f"Wildcard * search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Saturn Backup Cartridge - Japanese Manual"}, f"Wildcard * search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: WILDCARD * SEARCH <<<<<

    # >>>>> SEARCH TESTS: TAG INCLUSION (AND) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:wada rco, character:ereshkigal", groupby_tanks=False)
    )
    assert not error, f"Tag inclusion search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Fate GO MEMO"}, f"Tag inclusion search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: TAG INCLUSION (AND) <<<<<

    # >>>>> SEARCH TESTS: TAG EXCLUSION >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:wada rco, -character:ereshkigal", groupby_tanks=False)
    )
    assert not error, f"Tag exclusion search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Fate GO MEMO 2"}, f"Tag exclusion search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: TAG EXCLUSION <<<<<

    # >>>>> SEARCH TESTS: EXACT SEARCH WITH $ >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="character:segata$", groupby_tanks=False)
    )
    assert not error, f"Exact search with $ failed (status {error.status}): {error.error}"
    expected = {"Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    assert {r.title for r in response.data} == expected, f"Exact search with $ mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: EXACT SEARCH WITH $ <<<<<

    # >>>>> SEARCH TESTS: EXACT SEARCH WITH QUOTES >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Fate GO MEMO"', groupby_tanks=False)
    )
    assert not error, f"Exact search with quotes failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Fate GO MEMO"}, f"Exact search with quotes mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: EXACT SEARCH WITH QUOTES <<<<<

    # >>>>> SEARCH TESTS: STATIC CATEGORY >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(category=static_category_id, groupby_tanks=False)
    )
    assert not error, f"Static category search failed (status {error.status}): {error.error}"
    expected = {"Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    assert {r.title for r in response.data} == expected, f"Static category search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: STATIC CATEGORY <<<<<

    # >>>>> SEARCH TESTS: DYNAMIC CATEGORY + QUERY >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"character:segata"', category=dynamic_category_id, groupby_tanks=False)
    )
    assert not error, f"Dynamic category + query search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Saturn Backup Cartridge - American Manual"}, f"Dynamic category + query mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: DYNAMIC CATEGORY + QUERY <<<<<

    # >>>>> SEARCH TESTS: NEW FILTER >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(newonly=True, groupby_tanks=False)
    )
    assert not error, f"New filter search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"New Release Archive"}, f"New filter search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: NEW FILTER <<<<<

    # >>>>> SEARCH TESTS: UNTAGGED FILTER >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"Untagged filter search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Untagged Archive"}, f"Untagged filter search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: UNTAGGED FILTER <<<<<

    # >>>>> SEARCH TESTS: PAGECOUNT SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>150", groupby_tanks=False)
    )
    assert not error, f"Pagecount search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell", "Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    assert {r.title for r in response.data} == expected, f"Pagecount search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: PAGECOUNT SEARCH <<<<<

    # >>>>> SEARCH TESTS: READ COUNT EXACT >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:10", groupby_tanks=False)
    )
    assert not error, f"Read count exact search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell", "Saturn Backup Cartridge - Japanese Manual"}
    assert {r.title for r in response.data} == expected, f"Read count exact search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: READ COUNT EXACT <<<<<

    # >>>>> SEARCH TESTS: READ COUNT RANGE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:<10, read:>4", groupby_tanks=False)
    )
    assert not error, f"Read count range search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Saturn Backup Cartridge - American Manual"}, f"Read count range search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: READ COUNT RANGE <<<<<

    # >>>>> SORT TESTS: TITLE ASCENDING >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"Sort by title asc failed (status {error.status}): {error.error}"
    titles = [r.title for r in response.data]
    assert titles == sorted(titles), f"Sort by title asc not in order: {titles}"
    # <<<<< SORT TESTS: TITLE ASCENDING <<<<<

    # >>>>> SORT TESTS: TITLE DESCENDING >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="title", order="desc", groupby_tanks=False)
    )
    assert not error, f"Sort by title desc failed (status {error.status}): {error.error}"
    titles = [r.title for r in response.data]
    assert titles == sorted(titles, reverse=True), f"Sort by title desc not in order: {titles}"
    # <<<<< SORT TESTS: TITLE DESCENDING <<<<<

    # >>>>> SORT TESTS: LASTREAD DESCENDING >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="lastread", order="desc", groupby_tanks=False)
    )
    assert not error, f"Sort by lastread desc failed (status {error.status}): {error.error}"
    # Archives with reads should come first
    titles_with_reads = {"Ghost in the Shell", "Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    first_three_titles = {r.title for r in response.data[:3]}
    assert first_three_titles == titles_with_reads, f"Sort by lastread desc: expected read archives first, got {first_three_titles}"
    # <<<<< SORT TESTS: LASTREAD DESCENDING <<<<<

    # >>>>> TANKOUBON GROUPING TESTS: GROUPING OFF >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="vector", groupby_tanks=False)
    )
    assert not error, f"Tankoubon grouping off search failed (status {error.status}): {error.error}"
    expected = {"Medjed Collection", "Vector Art Book"}
    assert {r.title for r in response.data} == expected, f"Tankoubon grouping off mismatch: { {r.title for r in response.data} }"
    assert len(response.data) == 2, f"Expected 2 individual archives, got {len(response.data)}"
    # <<<<< TANKOUBON GROUPING TESTS: GROUPING OFF <<<<<

    # >>>>> TANKOUBON GROUPING TESTS: GROUPING ON >>>>>
    # Tankoubon grouping requires stat indexes to include tankoubon data.
    # Trigger a stat rebuild and wait for completion before testing.
    await trigger_stat_rebuild(lrr_client)
    LOGGER.debug("Stat hashes rebuilt for tankoubon grouping test.")

    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="vector", groupby_tanks=True)
    )
    assert not error, f"Tankoubon grouping on search failed (status {error.status}): {error.error}"
    # With grouping on, tankoubon members should be grouped; expect tankoubon ID in results
    arcids = [r.arcid for r in response.data]
    assert tankoubon_id in arcids, f"Tankoubon ID {tankoubon_id} not found in grouped results: {arcids}"
    # <<<<< TANKOUBON GROUPING TESTS: GROUPING ON <<<<<

    # >>>>> SEARCH TESTS: WILDCARD UNDERSCORE SEARCH >>>>>
    # Underscore (_) is a single-character wildcard, same as ?
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Fate GO MEMO _"', groupby_tanks=False)
    )
    assert not error, f"Wildcard underscore search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Fate GO MEMO 2"}, f"Wildcard underscore search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: WILDCARD UNDERSCORE SEARCH <<<<<

    # >>>>> SEARCH TESTS: WILDCARD PERCENT SEARCH >>>>>
    # Percent (%) is a multi-character wildcard, same as *
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Saturn%American%"', groupby_tanks=False)
    )
    assert not error, f"Wildcard percent search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Saturn Backup Cartridge - American Manual"}, f"Wildcard percent search mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: WILDCARD PERCENT SEARCH <<<<<

    # >>>>> SEARCH TESTS: EXACT SEARCH WITH QUOTES AND WILDCARD >>>>>
    # Quotes with wildcard and $ suffix for exact matching
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Saturn Backup Cartridge - *"$', groupby_tanks=False)
    )
    assert not error, f"Exact search with quotes and wildcard failed (status {error.status}): {error.error}"
    expected = {"Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    assert {r.title for r in response.data} == expected, f"Exact search with quotes and wildcard mismatch: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: EXACT SEARCH WITH QUOTES AND WILDCARD <<<<<

    # >>>>> SEARCH TESTS: MULTIPLE TOKENS WITH NON-MATCHING TERM >>>>>
    # When searching with multiple comma-separated terms, all terms are ANDed together.
    # If any term doesn't match, the entire search returns 0 results.
    # Here, "artist:shirow masamune" matches Ghost in the Shell, but "nonexistent_fake_xyz"
    # doesn't match any tags or titles, so the intersection is empty.
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:shirow masamune, nonexistent_fake_xyz", groupby_tanks=False)
    )
    assert not error, f"Multiple tokens with non-matching term search failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Multiple tokens with non-matching term should return 0 results, got {len(response.data)}: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: MULTIPLE TOKENS WITH NON-MATCHING TERM <<<<<

    # >>>>> SEARCH TESTS: INCORRECT TAG EXCLUSION SYNTAX >>>>>
    # When exclusion operator (-) is inside quotes, it's treated as literal text,
    # not as an exclusion operator. This searches for a title containing "-character:waver velvet".
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"artist:wada rco" "-character:waver velvet"', groupby_tanks=False)
    )
    assert not error, f"Incorrect tag exclusion syntax search failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Incorrect tag exclusion syntax should return 0 results, got {len(response.data)}: { {r.title for r in response.data} }"
    # <<<<< SEARCH TESTS: INCORRECT TAG EXCLUSION SYNTAX <<<<<

    # >>>>> DISCARD SEARCH CACHE >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"
    # <<<<< DISCARD SEARCH CACHE <<<<<

    # no error logs
    expect_no_error_logs(environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_pagecount_filter(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Page count filter functionality tests covering:
    - pages:N (exact match - no operator means equals)
    - pages:>N (greater than)
    - pages:>=N (greater than or equal)
    - pages:<N (less than)
    - pages:<=N (less than or equal)
    - Combined page filters (range queries)

    Note: The explicit `=` operator (pages:=N) is NOT supported by the implementation.
    The regex only matches `>`, `<`, `>=`, `<=`. Omitting the operator means exact match.
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
    # Archives with varying page counts to test all filter operators
    archive_specs = [
        {"name": "pages_5", "title": "Archive with 5 pages", "tags": "series:pagetest", "pages": 5},
        {"name": "pages_10", "title": "Archive with 10 pages", "tags": "series:pagetest", "pages": 10},
        {"name": "pages_15", "title": "Archive with 15 pages", "tags": "series:pagetest", "pages": 15},
        {"name": "pages_20", "title": "Archive with 20 pages", "tags": "series:pagetest", "pages": 20},
        {"name": "pages_25", "title": "Archive with 25 pages", "tags": "series:pagetest", "pages": 25},
        {"name": "pages_30", "title": "Archive with 30 pages", "tags": "series:pagetest", "pages": 30},
        {"name": "pages_50", "title": "Archive with 50 pages", "tags": "series:pagetest", "pages": 50},
        {"name": "pages_100", "title": "Archive with 100 pages", "tags": "series:pagetest", "pages": 100},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for pagecount filter testing.")

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


    # >>>>> PAGECOUNT TESTS: EXACT MATCH (NO OPERATOR) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:20", groupby_tanks=False)
    )
    assert not error, f"Pagecount exact (no op) failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Archive with 20 pages"}, \
        f"Pagecount exact mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount exact match (no operator) test passed.")
    # <<<<< PAGECOUNT TESTS: EXACT MATCH (NO OPERATOR) <<<<<

    # >>>>> PAGECOUNT TESTS: ANOTHER EXACT MATCH >>>>>
    # Note: The `=` operator is NOT supported in the implementation.
    # The regex only matches `>`, `<`, `>=`, `<=`. No operator means exact match.
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:15", groupby_tanks=False)
    )
    assert not error, f"Pagecount exact (15) failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Archive with 15 pages"}, \
        f"Pagecount exact (15) mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount exact match (15) test passed.")
    # <<<<< PAGECOUNT TESTS: ANOTHER EXACT MATCH <<<<<

    # >>>>> PAGECOUNT TESTS: GREATER THAN >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>25", groupby_tanks=False)
    )
    assert not error, f"Pagecount > failed (status {error.status}): {error.error}"
    expected = {"Archive with 30 pages", "Archive with 50 pages", "Archive with 100 pages"}
    assert {r.title for r in response.data} == expected, \
        f"Pagecount > mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount greater than test passed.")
    # <<<<< PAGECOUNT TESTS: GREATER THAN <<<<<

    # >>>>> PAGECOUNT TESTS: GREATER THAN OR EQUAL >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>=25", groupby_tanks=False)
    )
    assert not error, f"Pagecount >= failed (status {error.status}): {error.error}"
    expected = {"Archive with 25 pages", "Archive with 30 pages", "Archive with 50 pages", "Archive with 100 pages"}
    assert {r.title for r in response.data} == expected, \
        f"Pagecount >= mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount greater than or equal test passed.")
    # <<<<< PAGECOUNT TESTS: GREATER THAN OR EQUAL <<<<<

    # >>>>> PAGECOUNT TESTS: LESS THAN >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:<15", groupby_tanks=False)
    )
    assert not error, f"Pagecount < failed (status {error.status}): {error.error}"
    expected = {"Archive with 5 pages", "Archive with 10 pages"}
    assert {r.title for r in response.data} == expected, \
        f"Pagecount < mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount less than test passed.")
    # <<<<< PAGECOUNT TESTS: LESS THAN <<<<<

    # >>>>> PAGECOUNT TESTS: LESS THAN OR EQUAL >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:<=15", groupby_tanks=False)
    )
    assert not error, f"Pagecount <= failed (status {error.status}): {error.error}"
    expected = {"Archive with 5 pages", "Archive with 10 pages", "Archive with 15 pages"}
    assert {r.title for r in response.data} == expected, \
        f"Pagecount <= mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount less than or equal test passed.")
    # <<<<< PAGECOUNT TESTS: LESS THAN OR EQUAL <<<<<

    # >>>>> PAGECOUNT TESTS: COMBINED RANGE (BETWEEN) >>>>>
    # Find archives with 15 <= pages <= 30
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>=15, pages:<=30", groupby_tanks=False)
    )
    assert not error, f"Pagecount range failed (status {error.status}): {error.error}"
    expected = {"Archive with 15 pages", "Archive with 20 pages", "Archive with 25 pages", "Archive with 30 pages"}
    assert {r.title for r in response.data} == expected, \
        f"Pagecount range mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount combined range test passed.")
    # <<<<< PAGECOUNT TESTS: COMBINED RANGE (BETWEEN) <<<<<

    # >>>>> PAGECOUNT TESTS: EXCLUSIVE RANGE >>>>>
    # Find archives with 10 < pages < 30
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>10, pages:<30", groupby_tanks=False)
    )
    assert not error, f"Pagecount exclusive range failed (status {error.status}): {error.error}"
    expected = {"Archive with 15 pages", "Archive with 20 pages", "Archive with 25 pages"}
    assert {r.title for r in response.data} == expected, \
        f"Pagecount exclusive range mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount exclusive range test passed.")
    # <<<<< PAGECOUNT TESTS: EXCLUSIVE RANGE <<<<<

    # >>>>> PAGECOUNT TESTS: NO MATCHES >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>1000", groupby_tanks=False)
    )
    assert not error, f"Pagecount no match failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Expected 0 results for pages:>1000, got {len(response.data)}"
    LOGGER.debug("Pagecount no match test passed.")
    # <<<<< PAGECOUNT TESTS: NO MATCHES <<<<<

    # >>>>> PAGECOUNT TESTS: COMBINED WITH OTHER FILTERS >>>>>
    # Find archives with more than 20 pages AND matching title
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>20, Archive with", groupby_tanks=False)
    )
    assert not error, f"Pagecount with title filter failed (status {error.status}): {error.error}"
    expected = {"Archive with 25 pages", "Archive with 30 pages", "Archive with 50 pages", "Archive with 100 pages"}
    assert {r.title for r in response.data} == expected, \
        f"Pagecount with title filter mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Pagecount combined with title filter test passed.")
    # <<<<< PAGECOUNT TESTS: COMBINED WITH OTHER FILTERS <<<<<

    # no error logs
    expect_no_error_logs(environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_readcount_filter(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Read count (progress) filter functionality tests covering:
    - read:N (exact match)
    - read:>N (greater than)
    - read:>=N (greater than or equal)
    - read:<N (less than)
    - read:<=N (less than or equal)
    - Combined read filters (range queries)
    - read:0 for unread archives
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
    # Archives with enough pages to simulate various read counts
    archive_specs = [
        {"name": "read_unread", "title": "Unread Archive", "tags": "series:readtest", "pages": 50},
        {"name": "read_5", "title": "Archive Read 5 Pages", "tags": "series:readtest", "pages": 50},
        {"name": "read_10", "title": "Archive Read 10 Pages", "tags": "series:readtest", "pages": 50},
        {"name": "read_15", "title": "Archive Read 15 Pages", "tags": "series:readtest", "pages": 50},
        {"name": "read_20", "title": "Archive Read 20 Pages", "tags": "series:readtest", "pages": 50},
        {"name": "read_25", "title": "Archive Read 25 Pages", "tags": "series:readtest", "pages": 50},
        {"name": "read_30", "title": "Archive Read 30 Pages", "tags": "series:readtest", "pages": 50},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for read count filter testing.")

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

    # >>>>> SETUP: SIMULATE READS >>>>>
    read_specs = [
        ("Archive Read 5 Pages", 5),
        ("Archive Read 10 Pages", 10),
        ("Archive Read 15 Pages", 15),
        ("Archive Read 20 Pages", 20),
        ("Archive Read 25 Pages", 25),
        ("Archive Read 30 Pages", 30),
        # "Unread Archive" stays at 0 pages read
    ]
    for title, read_count in read_specs:
        arcid = title_to_arcid[title]
        for page in range(1, read_count + 1):
            response, error = await retry_on_lock(lambda a=arcid, p=page: lrr_client.archive_api.update_reading_progression(
                UpdateReadingProgressionRequest(arcid=a, page=p)
            ))
            assert not error, f"Failed to update progress for {title} (status {error.status}): {error.error}"
    LOGGER.debug("Simulated reading progress for 6 archives.")
    # <<<<< SETUP: SIMULATE READS <<<<<


    # >>>>> READCOUNT TESTS: EXACT MATCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:15", groupby_tanks=False)
    )
    assert not error, f"Readcount exact failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Archive Read 15 Pages"}, \
        f"Readcount exact mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount exact match test passed.")
    # <<<<< READCOUNT TESTS: EXACT MATCH <<<<<

    # >>>>> READCOUNT TESTS: UNREAD (EXACT 0) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:0", groupby_tanks=False)
    )
    assert not error, f"Readcount 0 (unread) failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Unread Archive"}, \
        f"Readcount 0 mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount 0 (unread) test passed.")
    # <<<<< READCOUNT TESTS: UNREAD (EXACT 0) <<<<<

    # >>>>> READCOUNT TESTS: GREATER THAN >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>20", groupby_tanks=False)
    )
    assert not error, f"Readcount > failed (status {error.status}): {error.error}"
    expected = {"Archive Read 25 Pages", "Archive Read 30 Pages"}
    assert {r.title for r in response.data} == expected, \
        f"Readcount > mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount greater than test passed.")
    # <<<<< READCOUNT TESTS: GREATER THAN <<<<<

    # >>>>> READCOUNT TESTS: GREATER THAN OR EQUAL >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>=20", groupby_tanks=False)
    )
    assert not error, f"Readcount >= failed (status {error.status}): {error.error}"
    expected = {"Archive Read 20 Pages", "Archive Read 25 Pages", "Archive Read 30 Pages"}
    assert {r.title for r in response.data} == expected, \
        f"Readcount >= mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount greater than or equal test passed.")
    # <<<<< READCOUNT TESTS: GREATER THAN OR EQUAL <<<<<

    # >>>>> READCOUNT TESTS: LESS THAN >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:<10", groupby_tanks=False)
    )
    assert not error, f"Readcount < failed (status {error.status}): {error.error}"
    expected = {"Unread Archive", "Archive Read 5 Pages"}
    assert {r.title for r in response.data} == expected, \
        f"Readcount < mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount less than test passed.")
    # <<<<< READCOUNT TESTS: LESS THAN <<<<<

    # >>>>> READCOUNT TESTS: LESS THAN OR EQUAL >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:<=10", groupby_tanks=False)
    )
    assert not error, f"Readcount <= failed (status {error.status}): {error.error}"
    expected = {"Unread Archive", "Archive Read 5 Pages", "Archive Read 10 Pages"}
    assert {r.title for r in response.data} == expected, \
        f"Readcount <= mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount less than or equal test passed.")
    # <<<<< READCOUNT TESTS: LESS THAN OR EQUAL <<<<<

    # >>>>> READCOUNT TESTS: COMBINED RANGE >>>>>
    # Find archives with 10 <= read <= 20
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>=10, read:<=20", groupby_tanks=False)
    )
    assert not error, f"Readcount range failed (status {error.status}): {error.error}"
    expected = {"Archive Read 10 Pages", "Archive Read 15 Pages", "Archive Read 20 Pages"}
    assert {r.title for r in response.data} == expected, \
        f"Readcount range mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount combined range test passed.")
    # <<<<< READCOUNT TESTS: COMBINED RANGE <<<<<

    # >>>>> READCOUNT TESTS: EXCLUSIVE RANGE >>>>>
    # Find archives with 5 < read < 25
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>5, read:<25", groupby_tanks=False)
    )
    assert not error, f"Readcount exclusive range failed (status {error.status}): {error.error}"
    expected = {"Archive Read 10 Pages", "Archive Read 15 Pages", "Archive Read 20 Pages"}
    assert {r.title for r in response.data} == expected, \
        f"Readcount exclusive range mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount exclusive range test passed.")
    # <<<<< READCOUNT TESTS: EXCLUSIVE RANGE <<<<<

    # >>>>> READCOUNT TESTS: ANY READ (>0) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>0", groupby_tanks=False)
    )
    assert not error, f"Readcount >0 failed (status {error.status}): {error.error}"
    # All archives except unread should be returned
    expected = {
        "Archive Read 5 Pages", "Archive Read 10 Pages", "Archive Read 15 Pages",
        "Archive Read 20 Pages", "Archive Read 25 Pages", "Archive Read 30 Pages"
    }
    assert {r.title for r in response.data} == expected, \
        f"Readcount >0 mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount >0 (any read) test passed.")
    # <<<<< READCOUNT TESTS: ANY READ (>0) <<<<<

    # >>>>> READCOUNT TESTS: COMBINED WITH PAGE FILTER >>>>>
    # Find archives with more than 10 pages read AND with 50 pages total
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>10, pages:50", groupby_tanks=False)
    )
    assert not error, f"Readcount + pagecount filter failed (status {error.status}): {error.error}"
    expected = {"Archive Read 15 Pages", "Archive Read 20 Pages", "Archive Read 25 Pages", "Archive Read 30 Pages"}
    assert {r.title for r in response.data} == expected, \
        f"Readcount + pagecount mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Readcount combined with pagecount filter test passed.")
    # <<<<< READCOUNT TESTS: COMBINED WITH PAGE FILTER <<<<<

    # no error logs
    expect_no_error_logs(environment)
