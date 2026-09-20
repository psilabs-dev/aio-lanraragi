"""
Natural sort integration tests for LANraragi.
"""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.search import SearchArchiveIndexRequest

from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    upload_archive,
)

LOGGER = logging.getLogger(__name__)

NATURAL_ASCENDING = ["Title 2", "Title 9", "Title 10", "Title 99", "Title 100", "Title 1000"]
NATURAL_DESCENDING = ["Title 1000", "Title 100", "Title 99", "Title 10", "Title 9", "Title 2"]
LEXICOGRAPHIC_ASCENDING = ["Title 10", "Title 100", "Title 1000", "Title 2", "Title 9", "Title 99"]


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
async def test_natural_sort(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Values embedding unpadded numbers must order by numeric value, so 9 precedes
    10, when sorting by a tag namespace (both directions) and by title.

    Elsewhere the suite pads its numeric tag values, which makes lexicographic
    and natural ordering identical and hides the difference. Archives are
    uploaded scrambled so insertion order cannot pass by accident.
    """

    # >>>>> TEST CONNECTION STAGE >>>>>
    _, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect to the LANraragi server (status {error.status}): {error.error}"

    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get all archives (status {error.status}): {error.error}"
    assert len(response.data) == 0, "Server contains archives!"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> ARCHIVE DEFINITION >>>>>
    archive_specs = [
        {"name": "archive_1", "title": "Title 99", "tags": "bookmarks:99", "pages": 3},
        {"name": "archive_2", "title": "Title 2", "tags": "bookmarks:2", "pages": 3},
        {"name": "archive_3", "title": "Title 1000", "tags": "bookmarks:1000", "pages": 3},
        {"name": "archive_4", "title": "Title 10", "tags": "bookmarks:10", "pages": 3},
        {"name": "archive_5", "title": "Title 100", "tags": "bookmarks:100", "pages": 3},
        {"name": "archive_6", "title": "Title 9", "tags": "bookmarks:9", "pages": 3},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        for spec in archive_specs:
            save_path = create_archive_file(tmpdir, spec["name"], spec["pages"])
            response, error = await upload_archive(
                lrr_client, save_path, save_path.name, semaphore,
                title=spec["title"], tags=spec["tags"],
            )
            assert not error, f"Upload failed for {spec['title']} (status {error.status}): {error.error}"
    del response, error
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # >>>>> ASCENDING NAMESPACE SORT >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="bookmarks", order="asc", start="-1", groupby_tanks=False)
    )
    assert not error, f"Ascending namespace sort failed (status {error.status}): {error.error}"
    ascending = []
    for record in response.data:
        ascending.append(record.title)
    assert ascending == NATURAL_ASCENDING, (
        f"Ascending sort by 'bookmarks' is not natural.\n"
        f"  expected: {NATURAL_ASCENDING}\n"
        f"  actual:   {ascending}\n"
        f"  a result of {LEXICOGRAPHIC_ASCENDING} means values are compared as text"
    )
    LOGGER.debug(f"Ascending namespace sort: {ascending}")
    # <<<<< ASCENDING NAMESPACE SORT <<<<<

    # >>>>> DESCENDING NAMESPACE SORT >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="bookmarks", order="desc", start="-1", groupby_tanks=False)
    )
    assert not error, f"Descending namespace sort failed (status {error.status}): {error.error}"
    descending = []
    for record in response.data:
        descending.append(record.title)
    assert descending == NATURAL_DESCENDING, (
        f"Descending sort by 'bookmarks' is not natural.\n"
        f"  expected: {NATURAL_DESCENDING}\n"
        f"  actual:   {descending}"
    )
    LOGGER.debug(f"Descending namespace sort: {descending}")
    # <<<<< DESCENDING NAMESPACE SORT <<<<<

    # >>>>> ASCENDING TITLE SORT >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(sortby="title", order="asc", start="-1", groupby_tanks=False)
    )
    assert not error, f"Title sort failed (status {error.status}): {error.error}"
    titles = []
    for record in response.data:
        titles.append(record.title)
    assert titles == NATURAL_ASCENDING, (
        f"Title sort is not natural.\n"
        f"  expected: {NATURAL_ASCENDING}\n"
        f"  actual:   {titles}"
    )
    LOGGER.debug(f"Ascending title sort: {titles}")
    # <<<<< ASCENDING TITLE SORT <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)
