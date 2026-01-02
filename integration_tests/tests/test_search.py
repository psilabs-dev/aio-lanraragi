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
from lanraragi.models.search import SearchArchiveIndexRequest
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
    yield "test_search_"


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

    # >>>>> DISCARD SEARCH CACHE >>>>>
    response, error = await lrr_client.search_api.discard_search_cache()
    assert not error, f"Failed to discard search cache (status {error.status}): {error.error}"
    # <<<<< DISCARD SEARCH CACHE <<<<<

    # no error logs
    expect_no_error_logs(environment)
