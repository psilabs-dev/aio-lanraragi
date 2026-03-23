"""
Integration tests for the composite search API (POST /api/search/composite).
"""

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.archive import ClearNewArchiveFlagRequest
from lanraragi.models.category import (
    AddArchiveToCategoryRequest,
    CreateCategoryRequest,
)
from lanraragi.models.search import (
    CompositeSearchCategoryEntry,
    CompositeSearchClause,
    CompositeSearchRequest,
)

from aio_lanraragi_tests.common import compute_archive_id
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    expect_no_error_logs,
)
from aio_lanraragi_tests.utils.api_wrappers import (
    create_archive_file,
    upload_archive,
)
from aio_lanraragi_tests.utils.concurrency import retry_on_lock

LOGGER = logging.getLogger(__name__)


@pytest.mark.flaky(reruns=2, condition=sys.platform == "win32", only_rerun=r"^ClientConnectorError")
@pytest.mark.asyncio
@pytest.mark.dev("search")
async def test_composite_search(
    lrr_client: LRRClient,
    semaphore: asyncio.Semaphore,
    environment: AbstractLRRDeploymentContext
):
    """
    Composite search API correctness test.

    1. Upload 10 archives with distinct tags.
    2. Create static categories for grouping.
    3. Test multi-clause OR with categories and namespace sort.
    4. Test duplicate clause deduplication.
    5. Test subset clause absorption.
    6. Test clause order idempotence.
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
        {"name": "archive_1", "title": "Fate GO MEMO", "tags": "artist:wada rco,character:ereshkigal", "pages": 3},
        {"name": "archive_2", "title": "Fate GO MEMO 2", "tags": "artist:wada rco,character:waver velvet", "pages": 3},
        {"name": "archive_3", "title": "Ghost in the Shell", "tags": "artist:shirow masamune,full color,artbook", "pages": 3},
        {"name": "archive_4", "title": "Saturn Japanese Manual", "tags": "character:segata sanshiro,region:japan", "pages": 3},
        {"name": "archive_5", "title": "Saturn American Manual", "tags": "character:segata,region:america", "pages": 3},
        {"name": "archive_6", "title": "Cool Collection 1", "tags": "series:cool,artist:alpha", "pages": 3},
        {"name": "archive_7", "title": "Cool Collection 2", "tags": "series:cool,artist:beta", "pages": 3},
        {"name": "archive_8", "title": "Cool Collection 3", "tags": "series:cool,artist:gamma", "pages": 3},
        {"name": "archive_9", "title": "New Release", "tags": "new_release", "pages": 3},
        {"name": "archive_10", "title": "Untagged Archive", "tags": "", "pages": 3},
    ]
    # <<<<< ARCHIVE DEFINITION <<<<<

    # >>>>> CREATE & UPLOAD ARCHIVES >>>>>
    title_to_arcid: dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
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

    LOGGER.debug(f"Uploaded {len(title_to_arcid)} archives.")
    # <<<<< CREATE & UPLOAD ARCHIVES <<<<<

    # >>>>> SETUP: CATEGORIES >>>>>
    # Static category: "Wada Rco" containing the 2 Fate archives
    response, error = await lrr_client.category_api.create_category(
        CreateCategoryRequest(name="Wada Rco")
    )
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    wada_category_id = response.category_id

    for title in ["Fate GO MEMO", "Fate GO MEMO 2"]:
        response, error = await retry_on_lock(lambda t=title: lrr_client.category_api.add_archive_to_category(
            AddArchiveToCategoryRequest(category_id=wada_category_id, arcid=title_to_arcid[t])
        ))
        assert not error, f"Failed to add {title} to category (status {error.status}): {error.error}"

    # Static category: "Saturn" containing the 2 Saturn archives
    response, error = await lrr_client.category_api.create_category(
        CreateCategoryRequest(name="Saturn")
    )
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    saturn_category_id = response.category_id

    for title in ["Saturn Japanese Manual", "Saturn American Manual"]:
        response, error = await retry_on_lock(lambda t=title: lrr_client.category_api.add_archive_to_category(
            AddArchiveToCategoryRequest(category_id=saturn_category_id, arcid=title_to_arcid[t])
        ))
        assert not error, f"Failed to add {title} to category (status {error.status}): {error.error}"

    # Static category: "Cool" containing 3 cool collection archives
    response, error = await lrr_client.category_api.create_category(
        CreateCategoryRequest(name="Cool")
    )
    assert not error, f"Failed to create category (status {error.status}): {error.error}"
    cool_category_id = response.category_id

    for title in ["Cool Collection 1", "Cool Collection 2", "Cool Collection 3"]:
        response, error = await retry_on_lock(lambda t=title: lrr_client.category_api.add_archive_to_category(
            AddArchiveToCategoryRequest(category_id=cool_category_id, arcid=title_to_arcid[t])
        ))
        assert not error, f"Failed to add {title} to category (status {error.status}): {error.error}"

    LOGGER.debug("Created 3 static categories.")
    # <<<<< SETUP: CATEGORIES <<<<<

    # >>>>> SETUP: CLEAR NEW FLAGS >>>>>
    for title, arcid in title_to_arcid.items():
        if title == "New Release":
            continue
        response, error = await retry_on_lock(lambda a=arcid: lrr_client.archive_api.clear_new_archive_flag(
            ClearNewArchiveFlagRequest(arcid=a)
        ))
        assert not error, f"Failed to clear new flag for {title} (status {error.status}): {error.error}"
    # <<<<< SETUP: CLEAR NEW FLAGS <<<<<

    # >>>>> TEST: MULTI-CLAUSE + CATEGORIES + NAMESPACE SORT >>>>>
    # Clause A: archives in "Wada Rco" category (Fate GO MEMO, Fate GO MEMO 2)
    # Clause B: archives in "Cool" category with filter "series:cool" (Cool Collection 1-3)
    # OR union should produce 5 archives total, sorted by artist namespace
    clause_a = CompositeSearchClause(
        categories=[CompositeSearchCategoryEntry(id=wada_category_id, mode="include")],
    )
    clause_b = CompositeSearchClause(
        filter="series:cool",
        categories=[CompositeSearchCategoryEntry(id=cool_category_id, mode="include")],
    )

    # Sort by artist ascending
    response, error = await lrr_client.search_api.composite_search(
        CompositeSearchRequest(clauses=[clause_a, clause_b], sortby="artist", order="asc", groupby_tanks=False)
    )
    assert not error, f"Composite search failed (status {error.status}): {error.error}"
    result_titles = {r.title for r in response.data}
    expected_titles = {"Fate GO MEMO", "Fate GO MEMO 2", "Cool Collection 1", "Cool Collection 2", "Cool Collection 3"}
    assert result_titles == expected_titles, f"Multi-clause OR mismatch: {result_titles}"

    # All 5 archives have artist: tags, verify sort boundary positions
    titles_asc = [r.title for r in response.data]
    assert titles_asc[0] == "Cool Collection 1", f"Artist asc first should be alpha: got {titles_asc[0]}"
    assert titles_asc[-1] in ("Fate GO MEMO", "Fate GO MEMO 2"), f"Artist asc last should be wada rco: got {titles_asc[-1]}"

    # Sort by artist descending
    response, error = await lrr_client.search_api.composite_search(
        CompositeSearchRequest(clauses=[clause_a, clause_b], sortby="artist", order="desc", groupby_tanks=False)
    )
    assert not error, f"Composite search desc failed (status {error.status}): {error.error}"
    titles_desc = [r.title for r in response.data]
    assert {r.title for r in response.data} == expected_titles, f"Desc sort should have same set: {titles_desc}"
    assert titles_desc[0] in ("Fate GO MEMO", "Fate GO MEMO 2"), f"Artist desc first should be wada rco: got {titles_desc[0]}"
    assert titles_desc[-1] == "Cool Collection 1", f"Artist desc last should be alpha: got {titles_desc[-1]}"

    LOGGER.debug("Multi-clause + categories + namespace sort test passed.")
    # <<<<< TEST: MULTI-CLAUSE + CATEGORIES + NAMESPACE SORT <<<<<

    # >>>>> TEST: DUPLICATE CLAUSES >>>>>
    # Two identical clauses should produce same results as single clause
    single_clause = CompositeSearchClause(filter="artist:wada rco")

    response_single, error = await lrr_client.search_api.composite_search(
        CompositeSearchRequest(clauses=[single_clause], groupby_tanks=False)
    )
    assert not error, f"Single clause search failed (status {error.status}): {error.error}"

    response_dup, error = await lrr_client.search_api.composite_search(
        CompositeSearchRequest(clauses=[single_clause, single_clause], groupby_tanks=False)
    )
    assert not error, f"Duplicate clause search failed (status {error.status}): {error.error}"

    single_arcids = [r.arcid for r in response_single.data]
    dup_arcids = [r.arcid for r in response_dup.data]
    assert len(dup_arcids) == len(set(dup_arcids)), f"Duplicate clauses produced duplicate arcids: {dup_arcids}"
    assert set(dup_arcids) == set(single_arcids), f"Duplicate clauses should match single clause: single={single_arcids}, dup={dup_arcids}"

    LOGGER.debug("Duplicate clause deduplication test passed.")
    # <<<<< TEST: DUPLICATE CLAUSES <<<<<

    # >>>>> TEST: SUBSET CLAUSE ABSORPTION >>>>>
    # Clause A: "artist:wada rco" (matches 2 archives)
    # Clause B: "artist:wada rco, character:ereshkigal" (matches 1 archive, subset of A)
    # Union should equal clause A's result
    clause_broad = CompositeSearchClause(filter="artist:wada rco")
    clause_narrow = CompositeSearchClause(filter="artist:wada rco, character:ereshkigal")

    response_broad, error = await lrr_client.search_api.composite_search(
        CompositeSearchRequest(clauses=[clause_broad], groupby_tanks=False)
    )
    assert not error, f"Broad clause search failed (status {error.status}): {error.error}"

    response_combined, error = await lrr_client.search_api.composite_search(
        CompositeSearchRequest(clauses=[clause_broad, clause_narrow], groupby_tanks=False)
    )
    assert not error, f"Combined clause search failed (status {error.status}): {error.error}"

    broad_arcids = [r.arcid for r in response_broad.data]
    combined_arcids = [r.arcid for r in response_combined.data]
    assert set(combined_arcids) == set(broad_arcids), f"Subset clause should be absorbed: broad={broad_arcids}, combined={combined_arcids}"
    assert len(combined_arcids) == len(broad_arcids), "Subset absorption should not increase result count"

    LOGGER.debug("Subset clause absorption test passed.")
    # <<<<< TEST: SUBSET CLAUSE ABSORPTION <<<<<

    # >>>>> TEST: CLAUSE ORDER IDEMPOTENCE >>>>>
    # [clause_a, clause_b] and [clause_b, clause_a] should produce identical results
    clause_x = CompositeSearchClause(filter="artist:wada rco")
    clause_y = CompositeSearchClause(filter="artist:shirow masamune")

    response_xy, error = await lrr_client.search_api.composite_search(
        CompositeSearchRequest(clauses=[clause_x, clause_y], sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"XY order search failed (status {error.status}): {error.error}"

    response_yx, error = await lrr_client.search_api.composite_search(
        CompositeSearchRequest(clauses=[clause_y, clause_x], sortby="title", order="asc", groupby_tanks=False)
    )
    assert not error, f"YX order search failed (status {error.status}): {error.error}"

    xy_arcids = [r.arcid for r in response_xy.data]
    yx_arcids = [r.arcid for r in response_yx.data]
    assert xy_arcids == yx_arcids, f"Clause order should not affect results: xy={xy_arcids}, yx={yx_arcids}"

    LOGGER.debug("Clause order idempotence test passed.")
    # <<<<< TEST: CLAUSE ORDER IDEMPOTENCE <<<<<

    # no error logs
    expect_no_error_logs(environment, LOGGER)
