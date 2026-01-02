"""
Search API integration tests for LANraragi.
"""

import asyncio
from http import HTTPMethod
import json
import logging
from pathlib import Path
import pytest
import pytest_asyncio
import sys
import tempfile
import time
from typing import Dict, Generator, Set, Tuple

from lanraragi.models.minion import GetMinionJobStatusRequest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import (
    ClearNewArchiveFlagRequest,
    UpdateReadingProgressionRequest,
)
from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse
from lanraragi.models.category import (
    AddArchiveToCategoryRequest,
    CreateCategoryRequest,
)
from lanraragi.models.search import GetRandomArchivesRequest, SearchArchiveIndexRequest
from lanraragi.models.tankoubon import (
    AddArchiveToTankoubonRequest,
    CreateTankoubonRequest,
)

from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.models import CreatePageRequest, WriteArchiveRequest
from aio_lanraragi_tests.common import compute_archive_id
from aio_lanraragi_tests.helpers import expect_no_error_logs, get_bounded_sem, upload_archive
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext

LOGGER = logging.getLogger(__name__)

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 11


@pytest.fixture
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)

    environments: Dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

    yield environment
    environment.teardown(remove_data=True)


@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield get_bounded_sem()

@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> Generator[LRRClient, None, None]:
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()

async def retry_on_lock(operation_func, max_retries: int = 10) -> Tuple[LanraragiResponse, LanraragiErrorResponse]:
    """Retry an operation if it encounters a 423 locked resource error."""
    retry_count = 0
    while True:
        response, error = await operation_func()
        if error and error.status == 423:
            retry_count += 1
            if retry_count > max_retries:
                return response, error
            await asyncio.sleep(2 ** retry_count)
            continue
        return response, error

def create_archive_file(tmpdir: Path, name: str, num_pages: int) -> Path:
    """Create a single archive with the specified number of pages."""
    filename = f"{name}.zip"
    save_path = tmpdir / filename

    create_page_requests = []
    for page_id in range(num_pages):
        page_text = f"{name}-pg-{str(page_id + 1).zfill(len(str(num_pages)))}"
        page_filename = f"{page_text}.png"
        create_page_requests.append(CreatePageRequest(
            width=100, height=100, filename=page_filename, image_format='PNG', text=page_text
        ))

    request = WriteArchiveRequest(
        create_page_requests=create_page_requests,
        save_path=save_path,
        archival_strategy=ArchivalStrategyEnum.ZIP
    )
    responses = write_archives_to_disk([request])
    assert responses[0].save_path == save_path
    return save_path


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

    # Helper function for assertions
    def get_result_titles(response) -> Set[str]:
        return {r.title for r in response.data}

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
    assert get_result_titles(response) == {"Ghost in the Shell"}, f"Title search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: BASIC TITLE SEARCH <<<<<

    # >>>>> SEARCH TESTS: EXACT NAMESPACE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"male:very cool"', groupby_tanks=False)
    )
    assert not error, f"Exact namespace search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Fate GO MEMO"}, f"Exact namespace search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: EXACT NAMESPACE SEARCH <<<<<

    # >>>>> SEARCH TESTS: FUZZY NAMESPACE SEARCH >>>>>
    # Fuzzy search: namespace must match exactly, value fuzzy-matches
    # "male:very cool" matches archives with namespace "male:" where value contains "very cool"
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="male:very cool", groupby_tanks=False)
    )
    assert not error, f"Fuzzy namespace search failed (status {error.status}): {error.error}"
    expected = {"Fate GO MEMO"}  # Only archive with "male:very cool" tag
    assert get_result_titles(response) == expected, f"Fuzzy namespace search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: FUZZY NAMESPACE SEARCH <<<<<

    # >>>>> SEARCH TESTS: VERY FUZZY NAMESPACE SEARCH >>>>>
    # Very fuzzy (*prefix): namespace can have prefix (e.g., "female" matches "*male")
    # "*male:very cool" matches namespaces ending with "male" (male, female) where value contains "very cool"
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="*male:very cool", groupby_tanks=False)
    )
    assert not error, f"Very fuzzy namespace search failed (status {error.status}): {error.error}"
    expected = {"Fate GO MEMO", "Saturn Backup Cartridge - American Manual"}  # male:very cool + female:very cool
    assert get_result_titles(response) == expected, f"Very fuzzy namespace search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: VERY FUZZY NAMESPACE SEARCH <<<<<

    # >>>>> SEARCH TESTS: WILDCARD ? SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Fate GO MEMO ?"', groupby_tanks=False)
    )
    assert not error, f"Wildcard ? search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Fate GO MEMO 2"}, f"Wildcard ? search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: WILDCARD ? SEARCH <<<<<

    # >>>>> SEARCH TESTS: WILDCARD * SEARCH >>>>>
    # Wildcard * in quotes matches full title pattern
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Saturn*Cartridge*Japanese Manual"', groupby_tanks=False)
    )
    assert not error, f"Wildcard * search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Saturn Backup Cartridge - Japanese Manual"}, f"Wildcard * search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: WILDCARD * SEARCH <<<<<

    # >>>>> SEARCH TESTS: TAG INCLUSION (AND) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:wada rco, character:ereshkigal", groupby_tanks=False)
    )
    assert not error, f"Tag inclusion search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Fate GO MEMO"}, f"Tag inclusion search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: TAG INCLUSION (AND) <<<<<

    # >>>>> SEARCH TESTS: TAG EXCLUSION >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:wada rco, -character:ereshkigal", groupby_tanks=False)
    )
    assert not error, f"Tag exclusion search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Fate GO MEMO 2"}, f"Tag exclusion search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: TAG EXCLUSION <<<<<

    # >>>>> SEARCH TESTS: EXACT SEARCH WITH $ >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="character:segata$", groupby_tanks=False)
    )
    assert not error, f"Exact search with $ failed (status {error.status}): {error.error}"
    expected = {"Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    assert get_result_titles(response) == expected, f"Exact search with $ mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: EXACT SEARCH WITH $ <<<<<

    # >>>>> SEARCH TESTS: EXACT SEARCH WITH QUOTES >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Fate GO MEMO"', groupby_tanks=False)
    )
    assert not error, f"Exact search with quotes failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Fate GO MEMO"}, f"Exact search with quotes mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: EXACT SEARCH WITH QUOTES <<<<<

    # >>>>> SEARCH TESTS: STATIC CATEGORY >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(category=static_category_id, groupby_tanks=False)
    )
    assert not error, f"Static category search failed (status {error.status}): {error.error}"
    expected = {"Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    assert get_result_titles(response) == expected, f"Static category search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: STATIC CATEGORY <<<<<

    # >>>>> SEARCH TESTS: DYNAMIC CATEGORY + QUERY >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"character:segata"', category=dynamic_category_id, groupby_tanks=False)
    )
    assert not error, f"Dynamic category + query search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Saturn Backup Cartridge - American Manual"}, f"Dynamic category + query mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: DYNAMIC CATEGORY + QUERY <<<<<

    # >>>>> SEARCH TESTS: NEW FILTER >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(newonly=True, groupby_tanks=False)
    )
    assert not error, f"New filter search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"New Release Archive"}, f"New filter search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: NEW FILTER <<<<<

    # >>>>> SEARCH TESTS: UNTAGGED FILTER >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"Untagged filter search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Untagged Archive"}, f"Untagged filter search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: UNTAGGED FILTER <<<<<

    # >>>>> SEARCH TESTS: PAGECOUNT SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>150", groupby_tanks=False)
    )
    assert not error, f"Pagecount search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell", "Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    assert get_result_titles(response) == expected, f"Pagecount search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: PAGECOUNT SEARCH <<<<<

    # >>>>> SEARCH TESTS: READ COUNT EXACT >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:10", groupby_tanks=False)
    )
    assert not error, f"Read count exact search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell", "Saturn Backup Cartridge - Japanese Manual"}
    assert get_result_titles(response) == expected, f"Read count exact search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: READ COUNT EXACT <<<<<

    # >>>>> SEARCH TESTS: READ COUNT RANGE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:<10, read:>4", groupby_tanks=False)
    )
    assert not error, f"Read count range search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Saturn Backup Cartridge - American Manual"}, f"Read count range search mismatch: {get_result_titles(response)}"
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
    assert get_result_titles(response) == expected, f"Tankoubon grouping off mismatch: {get_result_titles(response)}"
    assert len(response.data) == 2, f"Expected 2 individual archives, got {len(response.data)}"
    # <<<<< TANKOUBON GROUPING TESTS: GROUPING OFF <<<<<

    # >>>>> TANKOUBON GROUPING TESTS: GROUPING ON >>>>>
    # Tankoubon grouping requires stat indexes to include tankoubon data.
    # Trigger a stat rebuild and wait for completion before testing.

    status, content = await lrr_client.handle_request(
        HTTPMethod.POST,
        lrr_client.build_url("/api/minion/build_stat_hashes/queue"),
        lrr_client.headers,
        data={"args": "[]", "priority": "3"}
    )
    assert status == 200, f"Failed to queue build_stat_hashes: {content}"
    build_stat_hashes_data = json.loads(content)
    job_id = int(build_stat_hashes_data["job"])

    # Wait for stat rebuild to complete
    start_time = time.time()
    while True:
        assert time.time() - start_time < 60, "build_stat_hashes timed out after 60s"
        response, error = await lrr_client.minion_api.get_minion_job_status(
            GetMinionJobStatusRequest(job_id=job_id)
        )
        assert not error, f"Failed to get job status: {error.error}"
        state = response.state.lower()
        if state == "finished":
            break
        elif state == "failed":
            raise AssertionError("build_stat_hashes job failed")
        await asyncio.sleep(0.5)
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
    assert get_result_titles(response) == {"Fate GO MEMO 2"}, f"Wildcard underscore search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: WILDCARD UNDERSCORE SEARCH <<<<<

    # >>>>> SEARCH TESTS: WILDCARD PERCENT SEARCH >>>>>
    # Percent (%) is a multi-character wildcard, same as *
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Saturn%American%"', groupby_tanks=False)
    )
    assert not error, f"Wildcard percent search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Saturn Backup Cartridge - American Manual"}, f"Wildcard percent search mismatch: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: WILDCARD PERCENT SEARCH <<<<<

    # >>>>> SEARCH TESTS: EXACT SEARCH WITH QUOTES AND WILDCARD >>>>>
    # Quotes with wildcard and $ suffix for exact matching
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"Saturn Backup Cartridge - *"$', groupby_tanks=False)
    )
    assert not error, f"Exact search with quotes and wildcard failed (status {error.status}): {error.error}"
    expected = {"Saturn Backup Cartridge - Japanese Manual", "Saturn Backup Cartridge - American Manual"}
    assert get_result_titles(response) == expected, f"Exact search with quotes and wildcard mismatch: {get_result_titles(response)}"
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
    assert len(response.data) == 0, f"Multiple tokens with non-matching term should return 0 results, got {len(response.data)}: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: MULTIPLE TOKENS WITH NON-MATCHING TERM <<<<<

    # >>>>> SEARCH TESTS: INCORRECT TAG EXCLUSION SYNTAX >>>>>
    # When exclusion operator (-) is inside quotes, it's treated as literal text,
    # not as an exclusion operator. This searches for a title containing "-character:waver velvet".
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"artist:wada rco" "-character:waver velvet"', groupby_tanks=False)
    )
    assert not error, f"Incorrect tag exclusion syntax search failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Incorrect tag exclusion syntax should return 0 results, got {len(response.data)}: {get_result_titles(response)}"
    # <<<<< SEARCH TESTS: INCORRECT TAG EXCLUSION SYNTAX <<<<<

    # >>>>> DISCARD SEARCH CACHE >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"
    # <<<<< DISCARD SEARCH CACHE <<<<<

    # no error logs
    expect_no_error_logs(environment)

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

    # Helper function for assertions
    def get_result_titles(response) -> Set[str]:
        return {r.title for r in response.data}

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

    # Helper function for assertions
    def get_result_titles(response) -> Set[str]:
        return {r.title for r in response.data}

    # >>>>> PAGECOUNT TESTS: EXACT MATCH (NO OPERATOR) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:20", groupby_tanks=False)
    )
    assert not error, f"Pagecount exact (no op) failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Archive with 20 pages"}, \
        f"Pagecount exact mismatch: {get_result_titles(response)}"
    LOGGER.debug("Pagecount exact match (no operator) test passed.")
    # <<<<< PAGECOUNT TESTS: EXACT MATCH (NO OPERATOR) <<<<<

    # >>>>> PAGECOUNT TESTS: ANOTHER EXACT MATCH >>>>>
    # Note: The `=` operator is NOT supported in the implementation.
    # The regex only matches `>`, `<`, `>=`, `<=`. No operator means exact match.
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:15", groupby_tanks=False)
    )
    assert not error, f"Pagecount exact (15) failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Archive with 15 pages"}, \
        f"Pagecount exact (15) mismatch: {get_result_titles(response)}"
    LOGGER.debug("Pagecount exact match (15) test passed.")
    # <<<<< PAGECOUNT TESTS: ANOTHER EXACT MATCH <<<<<

    # >>>>> PAGECOUNT TESTS: GREATER THAN >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>25", groupby_tanks=False)
    )
    assert not error, f"Pagecount > failed (status {error.status}): {error.error}"
    expected = {"Archive with 30 pages", "Archive with 50 pages", "Archive with 100 pages"}
    assert get_result_titles(response) == expected, \
        f"Pagecount > mismatch: {get_result_titles(response)}"
    LOGGER.debug("Pagecount greater than test passed.")
    # <<<<< PAGECOUNT TESTS: GREATER THAN <<<<<

    # >>>>> PAGECOUNT TESTS: GREATER THAN OR EQUAL >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>=25", groupby_tanks=False)
    )
    assert not error, f"Pagecount >= failed (status {error.status}): {error.error}"
    expected = {"Archive with 25 pages", "Archive with 30 pages", "Archive with 50 pages", "Archive with 100 pages"}
    assert get_result_titles(response) == expected, \
        f"Pagecount >= mismatch: {get_result_titles(response)}"
    LOGGER.debug("Pagecount greater than or equal test passed.")
    # <<<<< PAGECOUNT TESTS: GREATER THAN OR EQUAL <<<<<

    # >>>>> PAGECOUNT TESTS: LESS THAN >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:<15", groupby_tanks=False)
    )
    assert not error, f"Pagecount < failed (status {error.status}): {error.error}"
    expected = {"Archive with 5 pages", "Archive with 10 pages"}
    assert get_result_titles(response) == expected, \
        f"Pagecount < mismatch: {get_result_titles(response)}"
    LOGGER.debug("Pagecount less than test passed.")
    # <<<<< PAGECOUNT TESTS: LESS THAN <<<<<

    # >>>>> PAGECOUNT TESTS: LESS THAN OR EQUAL >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:<=15", groupby_tanks=False)
    )
    assert not error, f"Pagecount <= failed (status {error.status}): {error.error}"
    expected = {"Archive with 5 pages", "Archive with 10 pages", "Archive with 15 pages"}
    assert get_result_titles(response) == expected, \
        f"Pagecount <= mismatch: {get_result_titles(response)}"
    LOGGER.debug("Pagecount less than or equal test passed.")
    # <<<<< PAGECOUNT TESTS: LESS THAN OR EQUAL <<<<<

    # >>>>> PAGECOUNT TESTS: COMBINED RANGE (BETWEEN) >>>>>
    # Find archives with 15 <= pages <= 30
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>=15, pages:<=30", groupby_tanks=False)
    )
    assert not error, f"Pagecount range failed (status {error.status}): {error.error}"
    expected = {"Archive with 15 pages", "Archive with 20 pages", "Archive with 25 pages", "Archive with 30 pages"}
    assert get_result_titles(response) == expected, \
        f"Pagecount range mismatch: {get_result_titles(response)}"
    LOGGER.debug("Pagecount combined range test passed.")
    # <<<<< PAGECOUNT TESTS: COMBINED RANGE (BETWEEN) <<<<<

    # >>>>> PAGECOUNT TESTS: EXCLUSIVE RANGE >>>>>
    # Find archives with 10 < pages < 30
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="pages:>10, pages:<30", groupby_tanks=False)
    )
    assert not error, f"Pagecount exclusive range failed (status {error.status}): {error.error}"
    expected = {"Archive with 15 pages", "Archive with 20 pages", "Archive with 25 pages"}
    assert get_result_titles(response) == expected, \
        f"Pagecount exclusive range mismatch: {get_result_titles(response)}"
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
    assert get_result_titles(response) == expected, \
        f"Pagecount with title filter mismatch: {get_result_titles(response)}"
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

    # Helper function for assertions
    def get_result_titles(response) -> Set[str]:
        return {r.title for r in response.data}

    # >>>>> READCOUNT TESTS: EXACT MATCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:15", groupby_tanks=False)
    )
    assert not error, f"Readcount exact failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Archive Read 15 Pages"}, \
        f"Readcount exact mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount exact match test passed.")
    # <<<<< READCOUNT TESTS: EXACT MATCH <<<<<

    # >>>>> READCOUNT TESTS: UNREAD (EXACT 0) >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:0", groupby_tanks=False)
    )
    assert not error, f"Readcount 0 (unread) failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Unread Archive"}, \
        f"Readcount 0 mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount 0 (unread) test passed.")
    # <<<<< READCOUNT TESTS: UNREAD (EXACT 0) <<<<<

    # >>>>> READCOUNT TESTS: GREATER THAN >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>20", groupby_tanks=False)
    )
    assert not error, f"Readcount > failed (status {error.status}): {error.error}"
    expected = {"Archive Read 25 Pages", "Archive Read 30 Pages"}
    assert get_result_titles(response) == expected, \
        f"Readcount > mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount greater than test passed.")
    # <<<<< READCOUNT TESTS: GREATER THAN <<<<<

    # >>>>> READCOUNT TESTS: GREATER THAN OR EQUAL >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>=20", groupby_tanks=False)
    )
    assert not error, f"Readcount >= failed (status {error.status}): {error.error}"
    expected = {"Archive Read 20 Pages", "Archive Read 25 Pages", "Archive Read 30 Pages"}
    assert get_result_titles(response) == expected, \
        f"Readcount >= mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount greater than or equal test passed.")
    # <<<<< READCOUNT TESTS: GREATER THAN OR EQUAL <<<<<

    # >>>>> READCOUNT TESTS: LESS THAN >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:<10", groupby_tanks=False)
    )
    assert not error, f"Readcount < failed (status {error.status}): {error.error}"
    expected = {"Unread Archive", "Archive Read 5 Pages"}
    assert get_result_titles(response) == expected, \
        f"Readcount < mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount less than test passed.")
    # <<<<< READCOUNT TESTS: LESS THAN <<<<<

    # >>>>> READCOUNT TESTS: LESS THAN OR EQUAL >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:<=10", groupby_tanks=False)
    )
    assert not error, f"Readcount <= failed (status {error.status}): {error.error}"
    expected = {"Unread Archive", "Archive Read 5 Pages", "Archive Read 10 Pages"}
    assert get_result_titles(response) == expected, \
        f"Readcount <= mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount less than or equal test passed.")
    # <<<<< READCOUNT TESTS: LESS THAN OR EQUAL <<<<<

    # >>>>> READCOUNT TESTS: COMBINED RANGE >>>>>
    # Find archives with 10 <= read <= 20
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>=10, read:<=20", groupby_tanks=False)
    )
    assert not error, f"Readcount range failed (status {error.status}): {error.error}"
    expected = {"Archive Read 10 Pages", "Archive Read 15 Pages", "Archive Read 20 Pages"}
    assert get_result_titles(response) == expected, \
        f"Readcount range mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount combined range test passed.")
    # <<<<< READCOUNT TESTS: COMBINED RANGE <<<<<

    # >>>>> READCOUNT TESTS: EXCLUSIVE RANGE >>>>>
    # Find archives with 5 < read < 25
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>5, read:<25", groupby_tanks=False)
    )
    assert not error, f"Readcount exclusive range failed (status {error.status}): {error.error}"
    expected = {"Archive Read 10 Pages", "Archive Read 15 Pages", "Archive Read 20 Pages"}
    assert get_result_titles(response) == expected, \
        f"Readcount exclusive range mismatch: {get_result_titles(response)}"
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
    assert get_result_titles(response) == expected, \
        f"Readcount >0 mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount >0 (any read) test passed.")
    # <<<<< READCOUNT TESTS: ANY READ (>0) <<<<<

    # >>>>> READCOUNT TESTS: COMBINED WITH PAGE FILTER >>>>>
    # Find archives with more than 10 pages read AND with 50 pages total
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="read:>10, pages:50", groupby_tanks=False)
    )
    assert not error, f"Readcount + pagecount filter failed (status {error.status}): {error.error}"
    expected = {"Archive Read 15 Pages", "Archive Read 20 Pages", "Archive Read 25 Pages", "Archive Read 30 Pages"}
    assert get_result_titles(response) == expected, \
        f"Readcount + pagecount mismatch: {get_result_titles(response)}"
    LOGGER.debug("Readcount combined with pagecount filter test passed.")
    # <<<<< READCOUNT TESTS: COMBINED WITH PAGE FILTER <<<<<

    # no error logs
    expect_no_error_logs(environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_multi_token_edge_cases(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Multi-token search edge case tests covering:
    - AND logic requires ALL tokens to match (missing tags = 0 results)
    - Unnamespaced tokens (no colon) ARE searched as tag values
    - Mixed namespaced and unnamespaced tokens
    - Multiple unnamespaced tokens
    - Whitespace/empty token handling
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
    # Note: The Perl test for "artist:shirow masamune, full color, artbook" returns 0 because
    # the mock archive with artist:shirow masamune does NOT have artbook/full color tags.
    # Unnamespaced tokens like "artbook" ARE searched as tags (not just titles).
    # AND logic requires ALL tokens to match.
    archive_specs = [
        # This archive has artist:shirow masamune but NOT artbook/full color - used to test AND logic
        {"name": "gits_artbook", "title": "Ghost in the Shell 1.5 - Human-Error Processor", "tags": "artist:shirow masamune", "pages": 34},
        {"name": "gits_manga", "title": "Ghost in the Shell Manga", "tags": "artist:shirow masamune, manga", "pages": 200},
        {"name": "fate_memo", "title": "Fate GO MEMO", "tags": "artist:wada rco, artbook, full color", "pages": 50},
        {"name": "fate_memo_2", "title": "Fate GO MEMO 2", "tags": "artist:wada rco, artbook, full color, character:ereshkigal", "pages": 30},
        {"name": "standalone_artbook", "title": "Standalone Artbook", "tags": "artbook, full color", "pages": 25},
        {"name": "no_tags", "title": "No Tags Archive", "tags": "", "pages": 10},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for multi-token edge case testing.")

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

    # Helper function for assertions
    def get_result_titles(response) -> Set[str]:
        return {r.title for r in response.data}

    # >>>>> MULTI-TOKEN TESTS: AND LOGIC WITH MISSING TAGS >>>>>
    # When searching with "artist:shirow masamune, full color, artbook", ALL tokens must match.
    # The archive with artist:shirow masamune does NOT have artbook/full color tags,
    # so the AND logic results in 0 matches. This mirrors the Perl unit test behavior.
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:shirow masamune, full color, artbook", groupby_tanks=False)
    )
    assert not error, f"Multi-token AND logic search failed (status {error.status}): {error.error}"
    # Returns 0 because no archive has ALL three: artist:shirow masamune + full color + artbook
    assert len(response.data) == 0, f"Expected 0 results (AND logic, missing tags), got {len(response.data)}: {get_result_titles(response)}"
    LOGGER.debug("Multi-token AND logic with missing tags test passed.")
    # <<<<< MULTI-TOKEN TESTS: AND LOGIC WITH MISSING TAGS <<<<<

    # >>>>> MULTI-TOKEN TESTS: UNNAMESPACED TOKENS AS TAGS >>>>>
    # Unnamespaced tokens like "artbook" and "full color" ARE searched as tag values.
    # This search should match archives that have both tags (even without namespace prefix).
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artbook, full color", groupby_tanks=False)
    )
    assert not error, f"Unnamespaced tag search failed (status {error.status}): {error.error}"
    # Archives with both artbook AND full color tags
    expected = {"Fate GO MEMO", "Fate GO MEMO 2", "Standalone Artbook"}
    assert get_result_titles(response) == expected, f"Unnamespaced tag search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Unnamespaced tokens as tags search test passed.")
    # <<<<< MULTI-TOKEN TESTS: UNNAMESPACED TOKENS AS TAGS <<<<<

    # >>>>> MULTI-TOKEN TESTS: SINGLE NAMESPACED TOKEN >>>>>
    # Searching with just the namespaced tag should return matching archives
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:shirow masamune", groupby_tanks=False)
    )
    assert not error, f"Single namespaced search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell 1.5 - Human-Error Processor", "Ghost in the Shell Manga"}
    assert get_result_titles(response) == expected, f"Single namespaced search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Single namespaced token search test passed.")
    # <<<<< MULTI-TOKEN TESTS: SINGLE NAMESPACED TOKEN <<<<<

    # >>>>> MULTI-TOKEN TESTS: UNNAMESPACED MATCHING TITLE >>>>>
    # Unnamespaced tokens that match titles should work
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:shirow masamune, Ghost", groupby_tanks=False)
    )
    assert not error, f"Unnamespaced title match search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell 1.5 - Human-Error Processor", "Ghost in the Shell Manga"}
    assert get_result_titles(response) == expected, f"Unnamespaced title match mismatch: {get_result_titles(response)}"
    LOGGER.debug("Unnamespaced token matching title search test passed.")
    # <<<<< MULTI-TOKEN TESTS: UNNAMESPACED MATCHING TITLE <<<<<

    # >>>>> MULTI-TOKEN TESTS: MULTIPLE NAMESPACED TOKENS >>>>>
    # Multiple namespaced tokens should work with AND logic
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:wada rco, character:ereshkigal", groupby_tanks=False)
    )
    assert not error, f"Multiple namespaced tokens search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Fate GO MEMO 2"}, f"Multiple namespaced tokens mismatch: {get_result_titles(response)}"
    LOGGER.debug("Multiple namespaced tokens search test passed.")
    # <<<<< MULTI-TOKEN TESTS: MULTIPLE NAMESPACED TOKENS <<<<<

    # >>>>> MULTI-TOKEN TESTS: UNNAMESPACED ONLY >>>>>
    # Multiple unnamespaced tokens are treated as title searches with AND logic
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Ghost, Shell", groupby_tanks=False)
    )
    assert not error, f"Multiple unnamespaced tokens search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell 1.5 - Human-Error Processor", "Ghost in the Shell Manga"}
    assert get_result_titles(response) == expected, f"Multiple unnamespaced tokens mismatch: {get_result_titles(response)}"
    LOGGER.debug("Multiple unnamespaced tokens search test passed.")
    # <<<<< MULTI-TOKEN TESTS: UNNAMESPACED ONLY <<<<<

    # >>>>> MULTI-TOKEN TESTS: NON-MATCHING UNNAMESPACED TOKEN >>>>>
    # Adding a non-matching unnamespaced token should filter out results
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Ghost, Nonexistent", groupby_tanks=False)
    )
    assert not error, f"Non-matching unnamespaced token search failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Expected 0 results with non-matching token, got {len(response.data)}"
    LOGGER.debug("Non-matching unnamespaced token search test passed.")
    # <<<<< MULTI-TOKEN TESTS: NON-MATCHING UNNAMESPACED TOKEN <<<<<

    # >>>>> MULTI-TOKEN TESTS: EMPTY TOKEN HANDLING >>>>>
    # Trailing comma should be handled gracefully
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Ghost, ", groupby_tanks=False)
    )
    assert not error, f"Trailing comma search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell 1.5 - Human-Error Processor", "Ghost in the Shell Manga"}
    assert get_result_titles(response) == expected, f"Trailing comma search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Empty token (trailing comma) handling test passed.")
    # <<<<< MULTI-TOKEN TESTS: EMPTY TOKEN HANDLING <<<<<

    # no error logs
    expect_no_error_logs(environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_unicode_search(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Unicode/UTF-8 character search tests covering:
    - Japanese (CJK) characters in titles
    - Japanese characters in tags
    - Mixed ASCII and Unicode
    - Unicode in namespace values
    - Special Unicode characters (emoji, symbols)
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
        {"name": "japanese_title", "title": "日本語タイトル", "tags": "artist:日本語作者, series:テスト", "pages": 20},
        {"name": "mixed_title", "title": "Mixed タイトル Test", "tags": "artist:mixed artist, series:混合シリーズ", "pages": 25},
        {"name": "korean_title", "title": "한국어 제목", "tags": "artist:한국작가, language:korean", "pages": 15},
        {"name": "chinese_title", "title": "中文标题", "tags": "artist:中文作者, language:chinese", "pages": 30},
        {"name": "cyrillic_title", "title": "Русский заголовок", "tags": "artist:русский автор, language:russian", "pages": 18},
        {"name": "ascii_only", "title": "ASCII Only Title", "tags": "artist:english artist, series:test", "pages": 10},
        {"name": "special_chars", "title": "Special ★ Characters ♪", "tags": "misc:★star★, misc:♪note♪", "pages": 12},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for Unicode search testing.")

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

    # Helper function for assertions
    def get_result_titles(response) -> Set[str]:
        return {r.title for r in response.data}

    # >>>>> UNICODE TESTS: JAPANESE TITLE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="日本語", groupby_tanks=False)
    )
    assert not error, f"Japanese title search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"日本語タイトル"}, f"Japanese title search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Japanese title search test passed.")
    # <<<<< UNICODE TESTS: JAPANESE TITLE SEARCH <<<<<

    # >>>>> UNICODE TESTS: JAPANESE TAG SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:日本語作者", groupby_tanks=False)
    )
    assert not error, f"Japanese tag search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"日本語タイトル"}, f"Japanese tag search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Japanese tag search test passed.")
    # <<<<< UNICODE TESTS: JAPANESE TAG SEARCH <<<<<

    # >>>>> UNICODE TESTS: MIXED ASCII AND JAPANESE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Mixed タイトル", groupby_tanks=False)
    )
    assert not error, f"Mixed ASCII/Japanese search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Mixed タイトル Test"}, f"Mixed search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Mixed ASCII and Japanese search test passed.")
    # <<<<< UNICODE TESTS: MIXED ASCII AND JAPANESE <<<<<

    # >>>>> UNICODE TESTS: KOREAN SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="한국어", groupby_tanks=False)
    )
    assert not error, f"Korean search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"한국어 제목"}, f"Korean search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Korean title search test passed.")
    # <<<<< UNICODE TESTS: KOREAN SEARCH <<<<<

    # >>>>> UNICODE TESTS: CHINESE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="中文", groupby_tanks=False)
    )
    assert not error, f"Chinese search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"中文标题"}, f"Chinese search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Chinese title search test passed.")
    # <<<<< UNICODE TESTS: CHINESE SEARCH <<<<<

    # >>>>> UNICODE TESTS: CYRILLIC SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Русский", groupby_tanks=False)
    )
    assert not error, f"Cyrillic search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Русский заголовок"}, f"Cyrillic search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Cyrillic title search test passed.")
    # <<<<< UNICODE TESTS: CYRILLIC SEARCH <<<<<

    # >>>>> UNICODE TESTS: SPECIAL SYMBOL SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="★", groupby_tanks=False)
    )
    assert not error, f"Special symbol search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"Special ★ Characters ♪"}, f"Special symbol search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Special symbol (★) search test passed.")
    # <<<<< UNICODE TESTS: SPECIAL SYMBOL SEARCH <<<<<

    # >>>>> UNICODE TESTS: UNICODE TAG NAMESPACE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="series:テスト", groupby_tanks=False)
    )
    assert not error, f"Unicode namespace search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"日本語タイトル"}, f"Unicode namespace search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Unicode tag namespace search test passed.")
    # <<<<< UNICODE TESTS: UNICODE TAG NAMESPACE SEARCH <<<<<

    # >>>>> UNICODE TESTS: EXACT UNICODE SEARCH WITH QUOTES >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"日本語タイトル"', groupby_tanks=False)
    )
    assert not error, f"Exact Unicode search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == {"日本語タイトル"}, f"Exact Unicode search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Exact Unicode search with quotes test passed.")
    # <<<<< UNICODE TESTS: EXACT UNICODE SEARCH WITH QUOTES <<<<<

    # >>>>> UNICODE TESTS: PARTIAL UNICODE MATCH >>>>>
    # Search for a partial Japanese string
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="タイトル", groupby_tanks=False)
    )
    assert not error, f"Partial Unicode search failed (status {error.status}): {error.error}"
    # Should match both "日本語タイトル" and "Mixed タイトル Test"
    expected = {"日本語タイトル", "Mixed タイトル Test"}
    assert get_result_titles(response) == expected, f"Partial Unicode search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Partial Unicode match search test passed.")
    # <<<<< UNICODE TESTS: PARTIAL UNICODE MATCH <<<<<

    # no error logs
    expect_no_error_logs(environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_combined_filters(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Combined filter tests covering:
    - newonly + untaggedonly simultaneously
    - newonly + category filter
    - untaggedonly + search terms
    - newonly + untaggedonly + category
    - All filters with search query
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
    # Create archives with different combinations of new/tagged states
    archive_specs = [
        {"name": "new_tagged", "title": "New Tagged Archive", "tags": "status:new, type:tagged", "pages": 10, "is_new": True},
        {"name": "new_untagged", "title": "New Untagged Archive", "tags": "", "pages": 15, "is_new": True},
        {"name": "old_tagged", "title": "Old Tagged Archive", "tags": "status:old, type:tagged", "pages": 20, "is_new": False},
        {"name": "old_untagged", "title": "Old Untagged Archive", "tags": "", "pages": 25, "is_new": False},
        {"name": "new_tagged_cat", "title": "New Tagged In Category", "tags": "status:new, type:category", "pages": 12, "is_new": True},
        {"name": "old_tagged_cat", "title": "Old Tagged In Category", "tags": "status:old, type:category", "pages": 18, "is_new": False},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for combined filter testing.")

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

    # >>>>> SETUP: CLEAR NEW FLAGS FOR "OLD" ARCHIVES >>>>>
    for spec in archive_specs:
        if not spec["is_new"]:
            arcid = title_to_arcid[spec["title"]]
            response, error = await retry_on_lock(lambda a=arcid: lrr_client.archive_api.clear_new_archive_flag(
                ClearNewArchiveFlagRequest(arcid=a)
            ))
            assert not error, f"Failed to clear new flag for {spec['title']} (status {error.status}): {error.error}"
    LOGGER.debug("Cleared new flags for old archives.")
    # <<<<< SETUP: CLEAR NEW FLAGS FOR "OLD" ARCHIVES <<<<<

    # >>>>> SETUP: CREATE CATEGORY >>>>>
    response, error = await lrr_client.category_api.create_category(
        CreateCategoryRequest(name="Test Category")
    )
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    category_id = response.category_id

    # Add specific archives to category
    for title in ["New Tagged In Category", "Old Tagged In Category"]:
        response, error = await retry_on_lock(lambda t=title: lrr_client.category_api.add_archive_to_category(
            AddArchiveToCategoryRequest(category_id=category_id, arcid=title_to_arcid[t])
        ))
        assert not error, f"Failed to add {title} to category (status {error.status}): {error.error}"
    LOGGER.debug("Created category with 2 archives.")
    # <<<<< SETUP: CREATE CATEGORY <<<<<

    # Helper function for assertions
    def get_result_titles(response) -> Set[str]:
        return {r.title for r in response.data}

    # >>>>> COMBINED FILTER TESTS: NEWONLY ALONE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(newonly=True, groupby_tanks=False)
    )
    assert not error, f"Newonly filter failed (status {error.status}): {error.error}"
    expected = {"New Tagged Archive", "New Untagged Archive", "New Tagged In Category"}
    assert get_result_titles(response) == expected, f"Newonly filter mismatch: {get_result_titles(response)}"
    LOGGER.debug("Newonly filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY ALONE <<<<<

    # >>>>> COMBINED FILTER TESTS: UNTAGGEDONLY ALONE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"Untaggedonly filter failed (status {error.status}): {error.error}"
    expected = {"New Untagged Archive", "Old Untagged Archive"}
    assert get_result_titles(response) == expected, f"Untaggedonly filter mismatch: {get_result_titles(response)}"
    LOGGER.debug("Untaggedonly filter test passed.")
    # <<<<< COMBINED FILTER TESTS: UNTAGGEDONLY ALONE <<<<<

    # >>>>> COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(newonly=True, untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"Newonly + untaggedonly filter failed (status {error.status}): {error.error}"
    # Should only return archives that are BOTH new AND untagged
    expected = {"New Untagged Archive"}
    assert get_result_titles(response) == expected, f"Newonly + untaggedonly filter mismatch: {get_result_titles(response)}"
    LOGGER.debug("Newonly + untaggedonly combined filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY <<<<<

    # >>>>> COMBINED FILTER TESTS: NEWONLY + CATEGORY >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(newonly=True, category=category_id, groupby_tanks=False)
    )
    assert not error, f"Newonly + category filter failed (status {error.status}): {error.error}"
    # Should only return new archives in the category
    expected = {"New Tagged In Category"}
    assert get_result_titles(response) == expected, f"Newonly + category filter mismatch: {get_result_titles(response)}"
    LOGGER.debug("Newonly + category filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY + CATEGORY <<<<<

    # >>>>> COMBINED FILTER TESTS: UNTAGGEDONLY + SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="New", untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"Untaggedonly + search filter failed (status {error.status}): {error.error}"
    # Should only return untagged archives matching "New" in title
    expected = {"New Untagged Archive"}
    assert get_result_titles(response) == expected, f"Untaggedonly + search filter mismatch: {get_result_titles(response)}"
    LOGGER.debug("Untaggedonly + search filter test passed.")
    # <<<<< COMBINED FILTER TESTS: UNTAGGEDONLY + SEARCH <<<<<

    # >>>>> COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY + SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Untagged", newonly=True, untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"All filters + search failed (status {error.status}): {error.error}"
    expected = {"New Untagged Archive"}
    assert get_result_titles(response) == expected, f"All filters + search mismatch: {get_result_titles(response)}"
    LOGGER.debug("Newonly + untaggedonly + search filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY + SEARCH <<<<<

    # >>>>> COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY (NO MATCH) >>>>>
    # Search for something that doesn't match any new+untagged archives
    # Note: "New Untagged Archive" contains "Tagged" as substring, so use a different term
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Category", newonly=True, untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"No match combined filter failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Expected 0 results, got {len(response.data)}: {get_result_titles(response)}"
    LOGGER.debug("No match combined filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY (NO MATCH) <<<<<

    # >>>>> COMBINED FILTER TESTS: CATEGORY + SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="type:category", category=category_id, groupby_tanks=False)
    )
    assert not error, f"Category + search filter failed (status {error.status}): {error.error}"
    expected = {"New Tagged In Category", "Old Tagged In Category"}
    assert get_result_titles(response) == expected, f"Category + search filter mismatch: {get_result_titles(response)}"
    LOGGER.debug("Category + search filter test passed.")
    # <<<<< COMBINED FILTER TESTS: CATEGORY + SEARCH <<<<<

    # no error logs
    expect_no_error_logs(environment)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_exclusion_only_search(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Exclusion-only search tests covering:
    - Search with only exclusion terms (no positive terms)
    - Multiple exclusion terms
    - Exclusion with exact match syntax
    - Exclusion with namespace
    - Exclusion combined with filters
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
        {"name": "artist_a", "title": "Archive by Artist A", "tags": "artist:alpha, genre:action", "pages": 10},
        {"name": "artist_b", "title": "Archive by Artist B", "tags": "artist:beta, genre:comedy", "pages": 15},
        {"name": "artist_c", "title": "Archive by Artist C", "tags": "artist:gamma, genre:action", "pages": 20},
        {"name": "artist_a_2", "title": "Second Archive by Artist A", "tags": "artist:alpha, genre:drama", "pages": 25},
        {"name": "no_artist", "title": "Archive Without Artist Tag", "tags": "genre:action", "pages": 12},
        {"name": "untagged", "title": "Completely Untagged", "tags": "", "pages": 8},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: Dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {len(archive_specs)} archives for exclusion-only search testing.")

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
    all_titles = set(title_to_arcid.keys())
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # Helper function for assertions
    def get_result_titles(response) -> Set[str]:
        return {r.title for r in response.data}

    # >>>>> EXCLUSION TESTS: SINGLE EXCLUSION >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-artist:alpha", groupby_tanks=False)
    )
    assert not error, f"Single exclusion search failed (status {error.status}): {error.error}"
    # Should return all archives EXCEPT those with artist:alpha
    expected = all_titles - {"Archive by Artist A", "Second Archive by Artist A"}
    assert get_result_titles(response) == expected, f"Single exclusion mismatch: {get_result_titles(response)}"
    LOGGER.debug("Single exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: SINGLE EXCLUSION <<<<<

    # >>>>> EXCLUSION TESTS: MULTIPLE EXCLUSIONS >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-artist:alpha, -artist:beta", groupby_tanks=False)
    )
    assert not error, f"Multiple exclusions search failed (status {error.status}): {error.error}"
    # Should exclude archives with artist:alpha OR artist:beta
    expected = {"Archive by Artist C", "Archive Without Artist Tag", "Completely Untagged"}
    assert get_result_titles(response) == expected, f"Multiple exclusions mismatch: {get_result_titles(response)}"
    LOGGER.debug("Multiple exclusions search test passed.")
    # <<<<< EXCLUSION TESTS: MULTIPLE EXCLUSIONS <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUSION WITH GENRE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-genre:action", groupby_tanks=False)
    )
    assert not error, f"Genre exclusion search failed (status {error.status}): {error.error}"
    # Should exclude archives with genre:action
    expected = {"Archive by Artist B", "Second Archive by Artist A", "Completely Untagged"}
    assert get_result_titles(response) == expected, f"Genre exclusion mismatch: {get_result_titles(response)}"
    LOGGER.debug("Genre exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUSION WITH GENRE <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUDE ALL MATCHING >>>>>
    # Exclude all archives with any artist tag using fuzzy match
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-artist:alpha, -artist:beta, -artist:gamma", groupby_tanks=False)
    )
    assert not error, f"Exclude all artists search failed (status {error.status}): {error.error}"
    expected = {"Archive Without Artist Tag", "Completely Untagged"}
    assert get_result_titles(response) == expected, f"Exclude all artists mismatch: {get_result_titles(response)}"
    LOGGER.debug("Exclude all matching artists test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUDE ALL MATCHING <<<<<

    # >>>>> EXCLUSION TESTS: POSITIVE + EXCLUSION >>>>>
    # Combine positive search term with exclusion
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="genre:action, -artist:alpha", groupby_tanks=False)
    )
    assert not error, f"Positive + exclusion search failed (status {error.status}): {error.error}"
    # genre:action matches: Artist A, Artist C, Without Artist Tag
    # Minus artist:alpha removes: Artist A
    expected = {"Archive by Artist C", "Archive Without Artist Tag"}
    assert get_result_titles(response) == expected, f"Positive + exclusion mismatch: {get_result_titles(response)}"
    LOGGER.debug("Positive + exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: POSITIVE + EXCLUSION <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUSION WITH TITLE SEARCH >>>>>
    # Exclusion combined with title search
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Archive, -artist:alpha", groupby_tanks=False)
    )
    assert not error, f"Title + exclusion search failed (status {error.status}): {error.error}"
    # Titles containing "Archive" (excludes "Completely Untagged"), minus artist:alpha
    expected = {"Archive by Artist B", "Archive by Artist C", "Archive Without Artist Tag"}
    assert get_result_titles(response) == expected, f"Title + exclusion mismatch: {get_result_titles(response)}"
    LOGGER.debug("Title + exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUSION WITH TITLE SEARCH <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUSION WITH EXACT MATCH >>>>>
    # Exclusion with exact match (quotes and $)
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='-"artist:alpha"', groupby_tanks=False)
    )
    assert not error, f"Exact exclusion search failed (status {error.status}): {error.error}"
    # Exact match for "artist:alpha" - should exclude archives with exactly that tag
    expected = all_titles - {"Archive by Artist A", "Second Archive by Artist A"}
    assert get_result_titles(response) == expected, f"Exact exclusion mismatch: {get_result_titles(response)}"
    LOGGER.debug("Exact exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUSION WITH EXACT MATCH <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUDE NONEXISTENT >>>>>
    # Excluding a nonexistent tag should return all archives
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-nonexistent:tag", groupby_tanks=False)
    )
    assert not error, f"Nonexistent exclusion search failed (status {error.status}): {error.error}"
    assert get_result_titles(response) == all_titles, f"Nonexistent exclusion should return all: {get_result_titles(response)}"
    LOGGER.debug("Nonexistent exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUDE NONEXISTENT <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUSION WITH NEWONLY FILTER >>>>>
    # Clear new flag for some archives first
    for title in ["Archive by Artist A", "Archive by Artist B", "Archive by Artist C"]:
        arcid = title_to_arcid[title]
        response, error = await retry_on_lock(lambda a=arcid: lrr_client.archive_api.clear_new_archive_flag(
            ClearNewArchiveFlagRequest(arcid=a)
        ))
        assert not error, f"Failed to clear new flag for {title}"

    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-artist:alpha", newonly=True, groupby_tanks=False)
    )
    assert not error, f"Exclusion + newonly filter failed (status {error.status}): {error.error}"
    # New archives: Second Archive by Artist A, Without Artist Tag, Untagged
    # Minus artist:alpha removes: Second Archive by Artist A
    expected = {"Archive Without Artist Tag", "Completely Untagged"}
    assert get_result_titles(response) == expected, f"Exclusion + newonly filter mismatch: {get_result_titles(response)}"
    LOGGER.debug("Exclusion + newonly filter test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUSION WITH NEWONLY FILTER <<<<<

    # no error logs
    expect_no_error_logs(environment)
