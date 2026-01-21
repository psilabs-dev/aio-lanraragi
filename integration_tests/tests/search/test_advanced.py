"""
Advanced search API integration tests for LANraragi.

These tests cover advanced search patterns and edge cases including:
- Multi-token search edge cases (AND logic, unnamespaced tokens)
- Unicode/UTF-8 character search (CJK, Cyrillic, special symbols)
- Combined filters (newonly + untaggedonly + category combinations)
- Exclusion-only searches

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
from lanraragi.models.archive import ClearNewArchiveFlagRequest
from lanraragi.models.category import (
    AddArchiveToCategoryRequest,
    CreateCategoryRequest,
)
from lanraragi.models.search import SearchArchiveIndexRequest

from aio_lanraragi_tests.common import compute_archive_id
from aio_lanraragi_tests.helpers import (
    create_archive_file,
    expect_no_error_logs,
    retry_on_lock,
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


    # >>>>> MULTI-TOKEN TESTS: AND LOGIC WITH MISSING TAGS >>>>>
    # When searching with "artist:shirow masamune, full color, artbook", ALL tokens must match.
    # The archive with artist:shirow masamune does NOT have artbook/full color tags,
    # so the AND logic results in 0 matches. This mirrors the Perl unit test behavior.
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:shirow masamune, full color, artbook", groupby_tanks=False)
    )
    assert not error, f"Multi-token AND logic search failed (status {error.status}): {error.error}"
    # Returns 0 because no archive has ALL three: artist:shirow masamune + full color + artbook
    assert len(response.data) == 0, f"Expected 0 results (AND logic, missing tags), got {len(response.data)}: { {r.title for r in response.data} }"
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
    assert {r.title for r in response.data} == expected, f"Unnamespaced tag search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Unnamespaced tokens as tags search test passed.")
    # <<<<< MULTI-TOKEN TESTS: UNNAMESPACED TOKENS AS TAGS <<<<<

    # >>>>> MULTI-TOKEN TESTS: SINGLE NAMESPACED TOKEN >>>>>
    # Searching with just the namespaced tag should return matching archives
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:shirow masamune", groupby_tanks=False)
    )
    assert not error, f"Single namespaced search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell 1.5 - Human-Error Processor", "Ghost in the Shell Manga"}
    assert {r.title for r in response.data} == expected, f"Single namespaced search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Single namespaced token search test passed.")
    # <<<<< MULTI-TOKEN TESTS: SINGLE NAMESPACED TOKEN <<<<<

    # >>>>> MULTI-TOKEN TESTS: UNNAMESPACED MATCHING TITLE >>>>>
    # Unnamespaced tokens that match titles should work
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:shirow masamune, Ghost", groupby_tanks=False)
    )
    assert not error, f"Unnamespaced title match search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell 1.5 - Human-Error Processor", "Ghost in the Shell Manga"}
    assert {r.title for r in response.data} == expected, f"Unnamespaced title match mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Unnamespaced token matching title search test passed.")
    # <<<<< MULTI-TOKEN TESTS: UNNAMESPACED MATCHING TITLE <<<<<

    # >>>>> MULTI-TOKEN TESTS: MULTIPLE NAMESPACED TOKENS >>>>>
    # Multiple namespaced tokens should work with AND logic
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:wada rco, character:ereshkigal", groupby_tanks=False)
    )
    assert not error, f"Multiple namespaced tokens search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Fate GO MEMO 2"}, f"Multiple namespaced tokens mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Multiple namespaced tokens search test passed.")
    # <<<<< MULTI-TOKEN TESTS: MULTIPLE NAMESPACED TOKENS <<<<<

    # >>>>> MULTI-TOKEN TESTS: UNNAMESPACED ONLY >>>>>
    # Multiple unnamespaced tokens are treated as title searches with AND logic
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Ghost, Shell", groupby_tanks=False)
    )
    assert not error, f"Multiple unnamespaced tokens search failed (status {error.status}): {error.error}"
    expected = {"Ghost in the Shell 1.5 - Human-Error Processor", "Ghost in the Shell Manga"}
    assert {r.title for r in response.data} == expected, f"Multiple unnamespaced tokens mismatch: { {r.title for r in response.data} }"
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
    assert {r.title for r in response.data} == expected, f"Trailing comma search mismatch: { {r.title for r in response.data} }"
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


    # >>>>> UNICODE TESTS: JAPANESE TITLE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="日本語", groupby_tanks=False)
    )
    assert not error, f"Japanese title search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"日本語タイトル"}, f"Japanese title search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Japanese title search test passed.")
    # <<<<< UNICODE TESTS: JAPANESE TITLE SEARCH <<<<<

    # >>>>> UNICODE TESTS: JAPANESE TAG SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="artist:日本語作者", groupby_tanks=False)
    )
    assert not error, f"Japanese tag search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"日本語タイトル"}, f"Japanese tag search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Japanese tag search test passed.")
    # <<<<< UNICODE TESTS: JAPANESE TAG SEARCH <<<<<

    # >>>>> UNICODE TESTS: MIXED ASCII AND JAPANESE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Mixed タイトル", groupby_tanks=False)
    )
    assert not error, f"Mixed ASCII/Japanese search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Mixed タイトル Test"}, f"Mixed search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Mixed ASCII and Japanese search test passed.")
    # <<<<< UNICODE TESTS: MIXED ASCII AND JAPANESE <<<<<

    # >>>>> UNICODE TESTS: KOREAN SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="한국어", groupby_tanks=False)
    )
    assert not error, f"Korean search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"한국어 제목"}, f"Korean search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Korean title search test passed.")
    # <<<<< UNICODE TESTS: KOREAN SEARCH <<<<<

    # >>>>> UNICODE TESTS: CHINESE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="中文", groupby_tanks=False)
    )
    assert not error, f"Chinese search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"中文标题"}, f"Chinese search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Chinese title search test passed.")
    # <<<<< UNICODE TESTS: CHINESE SEARCH <<<<<

    # >>>>> UNICODE TESTS: CYRILLIC SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Русский", groupby_tanks=False)
    )
    assert not error, f"Cyrillic search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Русский заголовок"}, f"Cyrillic search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Cyrillic title search test passed.")
    # <<<<< UNICODE TESTS: CYRILLIC SEARCH <<<<<

    # >>>>> UNICODE TESTS: SPECIAL SYMBOL SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="★", groupby_tanks=False)
    )
    assert not error, f"Special symbol search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"Special ★ Characters ♪"}, f"Special symbol search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Special symbol (★) search test passed.")
    # <<<<< UNICODE TESTS: SPECIAL SYMBOL SEARCH <<<<<

    # >>>>> UNICODE TESTS: UNICODE TAG NAMESPACE SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="series:テスト", groupby_tanks=False)
    )
    assert not error, f"Unicode namespace search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"日本語タイトル"}, f"Unicode namespace search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Unicode tag namespace search test passed.")
    # <<<<< UNICODE TESTS: UNICODE TAG NAMESPACE SEARCH <<<<<

    # >>>>> UNICODE TESTS: EXACT UNICODE SEARCH WITH QUOTES >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter='"日本語タイトル"', groupby_tanks=False)
    )
    assert not error, f"Exact Unicode search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == {"日本語タイトル"}, f"Exact Unicode search mismatch: { {r.title for r in response.data} }"
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
    assert {r.title for r in response.data} == expected, f"Partial Unicode search mismatch: { {r.title for r in response.data} }"
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


    # >>>>> COMBINED FILTER TESTS: NEWONLY ALONE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(newonly=True, groupby_tanks=False)
    )
    assert not error, f"Newonly filter failed (status {error.status}): {error.error}"
    expected = {"New Tagged Archive", "New Untagged Archive", "New Tagged In Category"}
    assert {r.title for r in response.data} == expected, f"Newonly filter mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Newonly filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY ALONE <<<<<

    # >>>>> COMBINED FILTER TESTS: UNTAGGEDONLY ALONE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"Untaggedonly filter failed (status {error.status}): {error.error}"
    expected = {"New Untagged Archive", "Old Untagged Archive"}
    assert {r.title for r in response.data} == expected, f"Untaggedonly filter mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Untaggedonly filter test passed.")
    # <<<<< COMBINED FILTER TESTS: UNTAGGEDONLY ALONE <<<<<

    # >>>>> COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(newonly=True, untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"Newonly + untaggedonly filter failed (status {error.status}): {error.error}"
    # Should only return archives that are BOTH new AND untagged
    expected = {"New Untagged Archive"}
    assert {r.title for r in response.data} == expected, f"Newonly + untaggedonly filter mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Newonly + untaggedonly combined filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY <<<<<

    # >>>>> COMBINED FILTER TESTS: NEWONLY + CATEGORY >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(newonly=True, category=category_id, groupby_tanks=False)
    )
    assert not error, f"Newonly + category filter failed (status {error.status}): {error.error}"
    # Should only return new archives in the category
    expected = {"New Tagged In Category"}
    assert {r.title for r in response.data} == expected, f"Newonly + category filter mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Newonly + category filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY + CATEGORY <<<<<

    # >>>>> COMBINED FILTER TESTS: UNTAGGEDONLY + SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="New", untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"Untaggedonly + search filter failed (status {error.status}): {error.error}"
    # Should only return untagged archives matching "New" in title
    expected = {"New Untagged Archive"}
    assert {r.title for r in response.data} == expected, f"Untaggedonly + search filter mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Untaggedonly + search filter test passed.")
    # <<<<< COMBINED FILTER TESTS: UNTAGGEDONLY + SEARCH <<<<<

    # >>>>> COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY + SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Untagged", newonly=True, untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"All filters + search failed (status {error.status}): {error.error}"
    expected = {"New Untagged Archive"}
    assert {r.title for r in response.data} == expected, f"All filters + search mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Newonly + untaggedonly + search filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY + SEARCH <<<<<

    # >>>>> COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY (NO MATCH) >>>>>
    # Search for something that doesn't match any new+untagged archives
    # Note: "New Untagged Archive" contains "Tagged" as substring, so use a different term
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="Category", newonly=True, untaggedonly=True, groupby_tanks=False)
    )
    assert not error, f"No match combined filter failed (status {error.status}): {error.error}"
    assert len(response.data) == 0, f"Expected 0 results, got {len(response.data)}: { {r.title for r in response.data} }"
    LOGGER.debug("No match combined filter test passed.")
    # <<<<< COMBINED FILTER TESTS: NEWONLY + UNTAGGEDONLY (NO MATCH) <<<<<

    # >>>>> COMBINED FILTER TESTS: CATEGORY + SEARCH >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="type:category", category=category_id, groupby_tanks=False)
    )
    assert not error, f"Category + search filter failed (status {error.status}): {error.error}"
    expected = {"New Tagged In Category", "Old Tagged In Category"}
    assert {r.title for r in response.data} == expected, f"Category + search filter mismatch: { {r.title for r in response.data} }"
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

    # >>>>> EXCLUSION TESTS: SINGLE EXCLUSION >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-artist:alpha", groupby_tanks=False)
    )
    assert not error, f"Single exclusion search failed (status {error.status}): {error.error}"
    # Should return all archives EXCEPT those with artist:alpha
    expected = all_titles - {"Archive by Artist A", "Second Archive by Artist A"}
    assert {r.title for r in response.data} == expected, f"Single exclusion mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Single exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: SINGLE EXCLUSION <<<<<

    # >>>>> EXCLUSION TESTS: MULTIPLE EXCLUSIONS >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-artist:alpha, -artist:beta", groupby_tanks=False)
    )
    assert not error, f"Multiple exclusions search failed (status {error.status}): {error.error}"
    # Should exclude archives with artist:alpha OR artist:beta
    expected = {"Archive by Artist C", "Archive Without Artist Tag", "Completely Untagged"}
    assert {r.title for r in response.data} == expected, f"Multiple exclusions mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Multiple exclusions search test passed.")
    # <<<<< EXCLUSION TESTS: MULTIPLE EXCLUSIONS <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUSION WITH GENRE >>>>>
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-genre:action", groupby_tanks=False)
    )
    assert not error, f"Genre exclusion search failed (status {error.status}): {error.error}"
    # Should exclude archives with genre:action
    expected = {"Archive by Artist B", "Second Archive by Artist A", "Completely Untagged"}
    assert {r.title for r in response.data} == expected, f"Genre exclusion mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Genre exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUSION WITH GENRE <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUDE ALL MATCHING >>>>>
    # Exclude all archives with any artist tag using fuzzy match
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-artist:alpha, -artist:beta, -artist:gamma", groupby_tanks=False)
    )
    assert not error, f"Exclude all artists search failed (status {error.status}): {error.error}"
    expected = {"Archive Without Artist Tag", "Completely Untagged"}
    assert {r.title for r in response.data} == expected, f"Exclude all artists mismatch: { {r.title for r in response.data} }"
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
    assert {r.title for r in response.data} == expected, f"Positive + exclusion mismatch: { {r.title for r in response.data} }"
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
    assert {r.title for r in response.data} == expected, f"Title + exclusion mismatch: { {r.title for r in response.data} }"
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
    assert {r.title for r in response.data} == expected, f"Exact exclusion mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Exact exclusion search test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUSION WITH EXACT MATCH <<<<<

    # >>>>> EXCLUSION TESTS: EXCLUDE NONEXISTENT >>>>>
    # Excluding a nonexistent tag should return all archives
    response, error = await lrr_client.search_api.search_archive_index(
        SearchArchiveIndexRequest(search_filter="-nonexistent:tag", groupby_tanks=False)
    )
    assert not error, f"Nonexistent exclusion search failed (status {error.status}): {error.error}"
    assert {r.title for r in response.data} == all_titles, f"Nonexistent exclusion should return all: { {r.title for r in response.data} }"
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
    assert {r.title for r in response.data} == expected, f"Exclusion + newonly filter mismatch: { {r.title for r in response.data} }"
    LOGGER.debug("Exclusion + newonly filter test passed.")
    # <<<<< EXCLUSION TESTS: EXCLUSION WITH NEWONLY FILTER <<<<<

    # no error logs
    expect_no_error_logs(environment)
