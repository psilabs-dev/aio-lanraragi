"""
Search algorithm test cases.

Tests the following functionalities live:
- filter accuracy
- sorting accuracy
"""

import asyncio
import hashlib
import logging
from pathlib import Path
import sys
import tempfile
from typing import Dict, Generator, List, Optional, Set, Tuple
import numpy as np
import pytest

from aio_lanraragi_tests.archive_generation.metadata import create_tag_generators, get_tag_assignments
from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.search_algorithm_testing.data_generation import dump_dataset, generate_dataset, validate_dataset
from aio_lanraragi_tests.search_algorithm_testing.models import S1ArchiveInfo, S1CategoryInfo
import pytest_asyncio

from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import UploadArchiveResponse
from lanraragi.models.base import LanraragiErrorResponse

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.common import compute_upload_checksum
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.models import (
    CreatePageRequest,
    WriteArchiveRequest,
    WriteArchiveResponse,
)
from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk

from lanraragi.models.search import SearchArchiveIndexRequest
from tests.utils import (
    pmf,
    upload_archive,
)

LOGGER = logging.getLogger(__name__)

@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"

@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture
def is_lrr_debug_mode(request: pytest.FixtureRequest) -> Generator[bool, None, None]:
    yield request.config.getoption("--lrr-debug")

@pytest.fixture
def dataset(request: pytest.FixtureRequest) -> Generator[Tuple[List[S1ArchiveInfo], List[S1CategoryInfo]], None, None]:
    seed: int = int(request.config.getoption("npseed"))
    yield generate_dataset(seed)

@pytest.fixture
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    environment.setup(with_api_key=True, with_nofunmode=True, lrr_debug_mode=is_lrr_debug_mode)
    request.session.lrr_environment = environment
    yield environment
    environment.teardown(remove_data=True)

@pytest_asyncio.fixture
async def lanraragi(environment: AbstractLRRDeploymentContext) ->  Generator[LRRClient, None, None]:
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()

@pytest.fixture
def npgenerator(request: pytest.FixtureRequest) -> Generator[np.random.Generator, None, None]:
    seed: int = int(request.config.getoption("npseed"))
    generator = np.random.default_rng(seed)
    yield generator

@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield asyncio.BoundedSemaphore(value=8)

def save_archives(num_archives: int, work_dir: Path, np_generator: np.random.Generator) -> List[WriteArchiveResponse]:
    requests = []
    responses = []
    for archive_id in range(num_archives):
        create_page_requests = []
        archive_name = f"archive-{str(archive_id+1).zfill(len(str(num_archives)))}"
        filename = f"{archive_name}.zip"
        save_path = work_dir / filename
        num_pages = np_generator.integers(10, 20)
        for page_id in range(num_pages):
            page_text = f"{archive_name}-pg-{str(page_id+1).zfill(len(str(num_pages)))}"
            page_filename = f"{page_text}.png"
            create_page_request = CreatePageRequest(1080, 1920, page_filename, image_format='PNG', text=page_text)
            create_page_requests.append(create_page_request)        
        requests.append(WriteArchiveRequest(create_page_requests, save_path, ArchivalStrategyEnum.ZIP))
    responses = write_archives_to_disk(requests)
    return responses

async def populate_server(
    lanraragi: LRRClient,
    semaphore: asyncio.Semaphore,
    s1_archives: List[S1ArchiveInfo]
) -> List[str]:
    """
    Create archive files for the given S1 dataset archives and upload them to the server.

    Returns list of uploaded arcids.
    """
    arcids: List[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        requests: List[WriteArchiveRequest] = []
        for a in s1_archives:
            create_page_requests: List[CreatePageRequest] = []
            num_pages = max(1, int(a.pages))
            for page_id in range(num_pages):
                page_text = f"s1-{a.preid}-pg-{str(page_id+1).zfill(len(str(num_pages)))}"
                page_filename = f"{page_text}.png"
                create_page_requests.append(
                    CreatePageRequest(1080, 1920, page_filename, image_format='PNG', text=page_text)
                )

            save_path = tmpdir_path / f"s1-{a.preid}.zip"
            requests.append(WriteArchiveRequest(create_page_requests, save_path, ArchivalStrategyEnum.ZIP))

        write_responses = write_archives_to_disk(requests)

        tasks = []
        for a, wr in zip(s1_archives, write_responses):
            checksum = compute_upload_checksum(wr.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(
                    lanraragi,
                    wr.save_path,
                    wr.save_path.name,
                    semaphore,
                    title=a.title,
                    tags=a.tags,
                    checksum=checksum,
                )
            ))

        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
            arcids.append(response.arcid)

    return arcids

def test_dataset_generation_quality(dataset: Tuple[List[S1ArchiveInfo], List[S1CategoryInfo]], request: pytest.FixtureRequest):
    """
    Pre-test that the dataset generated satsifies data quality constraints, and that the hash of the dataset is consistent and deterministic.
    """
    archives, categories = dataset
    validate_dataset(archives, categories)

    seed: int = int(request.config.getoption("npseed"))
    if seed == 42:
        sha256_hash = hashlib.sha256()
        sha256_hash.update(dump_dataset(archives, categories).encode())
        hex_digest = sha256_hash.hexdigest()
        assert hex_digest == "6481b592c90ddf2cfbf9aa11037f99511d0aa333611816e1a71651fec3c54521"
    else:
        LOGGER.warning("Nonstandard seed provided, skipping hash check.")

@pytest.mark.skipif(sys.platform != "win32", reason="Cache priming required only for flaky Windows testing environments.")
@pytest.mark.asyncio
@pytest.mark.xfail
async def test_xfail_catch_flakes(
    lanraragi: LRRClient, semaphore: asyncio.Semaphore, npgenerator: np.random.Generator
):
    """
    This xfail test case serves no integration testing purpose, other than to prime the cache of flaky testing hosts
    and reduce the chances of subsequent test case failures caused by network flakes, such as remote host connection
    closures or connection refused errors resulting from high client request pressure to unprepared host.

    Therefore, occasional test case failures here are expected and ignored.
    """
    num_archives = 100

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    LOGGER.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> UPLOAD STAGE >>>>>
    tag_generators = create_tag_generators(100, pmf)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.debug(f"Creating {num_archives} archives to upload.")
        write_responses = save_archives(num_archives, tmpdir, npgenerator)
        assert len(write_responses) == num_archives, f"Number of archives written does not equal {num_archives}!"

        # archive metadata
        LOGGER.debug("Uploading archives to server.")
        tasks = []
        for i, _response in enumerate(write_responses):
            title = f"Archive {i}"
            tags = ','.join(get_tag_assignments(tag_generators, npgenerator))
            checksum = compute_upload_checksum(_response.save_path)
            tasks.append(asyncio.create_task(
                upload_archive(lanraragi, _response.save_path, _response.save_path.name, semaphore, title=title, tags=tags, checksum=checksum)
            ))
        gathered: List[Tuple[UploadArchiveResponse, LanraragiErrorResponse]] = await asyncio.gather(*tasks)
        for response, error in gathered:
            assert not error, f"Upload failed (status {error.status}): {error.error}"
        del response, error
    # <<<<< UPLOAD STAGE <<<<<

@pytest.mark.asyncio
async def test_search_algorithm(
    dataset: Tuple[List[S1ArchiveInfo], List[S1CategoryInfo]], environment: AbstractLRRDeploymentContext, lanraragi: LRRClient, semaphore: asyncio.Semaphore, is_lrr_debug_mode: bool
):
    """
    Test the search algorithm.

    Cases to test:
    - test exact title search
    - test sorting algorithm
    """

    environment.setup(with_nofunmode=True, with_api_key=True, lrr_debug_mode=is_lrr_debug_mode)

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lanraragi.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    LOGGER.debug("Established connection with test LRR server.")
    # verify we are working with a new server.
    response, error = await lanraragi.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> INITIALIZE DATASET >>>>>
    LOGGER.info("Initializing dataset...")
    archives, _ = dataset
    await populate_server(lanraragi, semaphore, archives)
    LOGGER.info("Dataset initialization complete.")
    # <<<<< INITIALIZE DATASET <<<<<

    # >>>>> SEARCH TESTS >>>>>
    # search by title with expectation of only one result.
    title_frequency_map: Dict[str, List[S1ArchiveInfo]] = {}
    for a in archives:
        if a.title not in title_frequency_map:
            title_frequency_map[a.title] = []
        title_frequency_map[a.title].append(a)
    expected_title: Optional[str] = None
    expected_tagset: Optional[Set[str]] = None
    for title in title_frequency_map:
        if len(title_frequency_map[title]) == 1:
            expected_title = title_frequency_map[title][0].title
            expected_tagset = set(title_frequency_map[title][0].tags.split(","))
            break
    assert expected_title, "No archive found with unique title!"
    response, error = await lanraragi.search_api.search_archive_index(SearchArchiveIndexRequest(search_filter=expected_title))
    assert not error, f"Search failed (status {error.status}): {error.error}"
    assert len(response.data) == 1, f"Expected exactly one archive, got: {response.data}"
    actual_title = response.data[0].title
    actual_tagset = set(response.data[0].tags.split(","))
    assert actual_title == expected_title, f"Expected title {expected_title}, got: {actual_title}"
    assert actual_tagset.issuperset(expected_tagset), f"Actual tagset {actual_tagset} not a subset of expected tagset: {expected_tagset} (extra tags: {expected_tagset.difference(actual_tagset)})"
    response, error = await lanraragi.search_api.discard_search_cache()
    assert not error, f"Discard search cache failed (status {error.status}): {error.error}"

    # search untaggedonly archives, sort by title (asc then desc)

    # search by most popular artist and sort by source (desc then asc, then include start flag)

    # search by most popular artist, include a category, then sort by another namespace (asc then desc)

    # <<<<< SEARCH TESTS <<<<<
