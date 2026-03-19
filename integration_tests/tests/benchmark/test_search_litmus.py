"""
Search benchmark litmus test.

Uploads archives with realistic Zipfian tag distributions across multiple
namespaces, then times representative search queries that exercise tag
filtering, namespace sorting (including natural sort), pagination, and
cache behavior. Each query is run twice within the same deployment
(cold, warm) to capture idempotence characteristics.

Scale is chosen to make Perl-side tag tokenization, namespace extraction,
and natural sort comparison observable in timing data.
"""

import asyncio
import logging
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest
from lanraragi.clients.client import LRRClient
from lanraragi.models.search import SearchArchiveIndexRequest

from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.metadata.zipf_utils import (
    get_archive_idx_to_tag_idxs_map,
)
from aio_lanraragi_tests.archive_generation.models import (
    CreatePageRequest,
    WriteArchiveRequest,
)
from aio_lanraragi_tests.benchmarking import BenchmarkCollector
from aio_lanraragi_tests.utils.api_wrappers import (
    trigger_stat_rebuild,
    upload_archive,
)

LOGGER = logging.getLogger(__name__)


def _generate_archives(num_archives: int, work_dir: Path):
    """Generate lightweight single-page archives for benchmarking."""
    num_digits = len(str(num_archives))
    num_shards = (num_archives - 1) // 1000 + 1
    shard_digits = len(str(max(0, num_shards - 1)))

    requests = []
    for arcidx in range(num_archives):
        archive_name = f"bench-{str(arcidx).zfill(num_digits)}"
        shard = str(arcidx // 1000).zfill(shard_digits or 1)
        shard_dir = work_dir / shard
        shard_dir.mkdir(parents=True, exist_ok=True)

        page = CreatePageRequest(
            width=100, height=100,
            filename=f"{archive_name}-pg-1.png",
            image_format="PNG",
            text=archive_name,
        )
        requests.append(WriteArchiveRequest(
            create_page_requests=[page],
            save_path=shard_dir / f"{archive_name}.zip",
            archival_strategy=ArchivalStrategyEnum.ZIP,
        ))

    return write_archives_to_disk(requests)


def _build_tag_strings(
    num_archives: int,
    num_tags: int,
    num_artists: int,
    num_sources: int,
    num_series: int,
    npgenerator: np.random.Generator,
) -> list[str]:
    """Build per-archive tag strings with realistic multi-namespace Zipfian distribution."""
    # General tags: 1-20 per archive from a pool of num_tags
    general_map = get_archive_idx_to_tag_idxs_map(
        num_archives, num_tags, 1, 20, npgenerator,
    )
    # Artist namespace: ~60% of archives get 1 artist
    artist_map = get_archive_idx_to_tag_idxs_map(
        num_archives, num_artists, 0, 1, npgenerator, poisson_lam=0.6,
    )
    # Source namespace: ~40% of archives get 1 source
    source_map = get_archive_idx_to_tag_idxs_map(
        num_archives, num_sources, 0, 1, npgenerator, poisson_lam=0.4,
    )
    # Series namespace: ~20% of archives belong to a series
    series_map = get_archive_idx_to_tag_idxs_map(
        num_archives, num_series, 0, 1, npgenerator, poisson_lam=0.2,
    )

    tag_strings = []
    for arcidx in range(num_archives):
        tags = [f"tag-{idx}" for idx in general_map.get(arcidx, [])]

        for aidx in artist_map.get(arcidx, []):
            tags.append(f"artist:artist-{aidx}")

        for sidx in source_map.get(arcidx, []):
            tags.append(f"source:https://example.org/gallery/{sidx}")

        for sridx in series_map.get(arcidx, []):
            tags.append(f"series:series-{sridx}")

        # date_added with realistic epoch spread
        epoch = int(npgenerator.integers(1_600_000_000, 1_760_000_000))
        tags.append(f"date_added:{epoch}")

        tag_strings.append(",".join(tags))

    return tag_strings


async def _timed_search(
    lrr_client: LRRClient,
    request: SearchArchiveIndexRequest,
) -> tuple[float, int]:
    """Execute a search and return (elapsed_seconds, result_count)."""
    start = time.perf_counter()
    response, error = await lrr_client.search_api.search_archive_index(request)
    elapsed = time.perf_counter() - start
    assert not error, f"Search failed: {error.error}"
    return elapsed, response.records_filtered


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_search_litmus(
    lrr_client: LRRClient,
    npgenerator: np.random.Generator,
    benchmark_collector: BenchmarkCollector,
):
    """
    Benchmark litmus test for search performance.

    1. Generate and upload archives with Zipfian tag distribution.
    2. Trigger stat rebuild to populate search indexes.
    3. Run each query twice (cold, warm).
    4. Record timings to the benchmark collector.
    """
    num_archives = 100_000
    num_tags = 100_000
    num_artists = 5_000
    num_sources = 3_000
    num_series = 10_000
    search_iterations = 2
    upload_concurrency = 16

    # >>>>> TEST CONNECTION STAGE >>>>>
    response, error = await lrr_client.misc_api.get_server_info()
    assert not error, f"Failed to connect: {error.error}"
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get archives: {error.error}"
    assert len(response.data) == 0, "Server already contains archives"
    del response, error
    # <<<<< TEST CONNECTION STAGE <<<<<

    # >>>>> GENERATION + UPLOAD STAGE >>>>>
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        LOGGER.info(f"Generating {num_archives} lightweight archives...")
        gen_start = time.perf_counter()
        write_responses = _generate_archives(num_archives, tmpdir)
        gen_elapsed = time.perf_counter() - gen_start
        LOGGER.info(f"Generation complete in {gen_elapsed:.1f}s")

        LOGGER.info("Building tag distributions...")
        tag_strings = _build_tag_strings(num_archives, num_tags, num_artists, num_sources, num_series, npgenerator)

        LOGGER.info(f"Uploading {num_archives} archives (concurrency={upload_concurrency})...")
        upload_sem = asyncio.BoundedSemaphore(upload_concurrency)
        upload_start = time.perf_counter()

        tasks = []
        for arcidx, wr in enumerate(write_responses):
            title = f"Archive {arcidx}"
            tasks.append(
                upload_archive(
                    lrr_client, wr.save_path, wr.save_path.name, upload_sem,
                    title=title, tags=tag_strings[arcidx],
                    retry_on_ise=True,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        exceptions = [(i, r) for i, r in enumerate(results) if isinstance(r, BaseException)]
        assert not exceptions, f"{len(exceptions)} uploads raised, first: {exceptions[0][1]}"
        failed = [(i, err) for i, (_, err) in enumerate(results) if err]
        assert not failed, f"{len(failed)} uploads failed, first: {failed[0][1].error}"

        upload_elapsed = time.perf_counter() - upload_start
        LOGGER.info(f"Upload complete in {upload_elapsed:.1f}s")

    # Verify server-side archive count
    response, error = await lrr_client.archive_api.get_all_archives()
    assert not error, f"Failed to get archives post-upload: {error.error}"
    assert len(response.data) == num_archives, (
        f"Server has {len(response.data)} archives, expected {num_archives}"
    )
    del response, error
    # <<<<< GENERATION + UPLOAD STAGE <<<<<

    # >>>>> STAT REBUILD >>>>>
    LOGGER.info("Triggering stat rebuild...")
    rebuild_start = time.perf_counter()
    stat_rebuild_timeout = max(600, num_archives // 100)
    await trigger_stat_rebuild(lrr_client, timeout_seconds=stat_rebuild_timeout)
    rebuild_elapsed = time.perf_counter() - rebuild_start
    benchmark_collector.record("_stat_rebuild", 0, rebuild_elapsed)
    LOGGER.info(f"Stat rebuild complete in {rebuild_elapsed:.1f}s")
    # <<<<< STAT REBUILD <<<<<

    # >>>>> BENCHMARK QUERIES >>>>>
    queries = {
        # Unfiltered sort (exercises full archive set natural sort)
        "unfiltered_title_asc": SearchArchiveIndexRequest(
            sortby="title", order="asc",
        ),
        "unfiltered_title_desc": SearchArchiveIndexRequest(
            sortby="title", order="desc",
        ),

        # Tag filter (Perl-side tokenization + Redis INDEX lookup vs SQL)
        "filter_common_tag": SearchArchiveIndexRequest(
            search_filter="tag-0",
        ),
        "filter_rare_tag": SearchArchiveIndexRequest(
            search_filter=f"tag-{num_tags - 1}",
        ),

        # Namespace filter (exercises namespace extraction)
        "filter_artist_namespace": SearchArchiveIndexRequest(
            search_filter="artist:artist-0",
        ),
        "filter_series_namespace": SearchArchiveIndexRequest(
            search_filter="series:series-0",
        ),

        # Exact match (exercises = / LOWER() / CITEXT path in PgSearch)
        "filter_artist_exact": SearchArchiveIndexRequest(
            search_filter="artist:artist-0$",
        ),
        "filter_series_exact": SearchArchiveIndexRequest(
            search_filter="series:series-0$",
        ),
        "filter_tag_exact": SearchArchiveIndexRequest(
            search_filter="tag-0$",
        ),

        # Namespace sort with keyed/unkeyed partition (the #1473 pattern)
        "sort_by_artist_asc": SearchArchiveIndexRequest(
            sortby="artist", order="asc",
        ),
        "sort_by_artist_desc": SearchArchiveIndexRequest(
            sortby="artist", order="desc",
        ),

        # Compound: filter + namespace sort (filter narrows, then sort remaining)
        "compound_filter_tag0_sort_artist": SearchArchiveIndexRequest(
            search_filter="tag-0", sortby="artist", order="asc",
        ),

        # Namespace sort: varying coverage levels
        # series (~20% coverage)
        "sort_by_series_asc": SearchArchiveIndexRequest(
            sortby="series", order="asc",
        ),
        "sort_by_series_desc": SearchArchiveIndexRequest(
            sortby="series", order="desc",
        ),
        # source (~40% coverage)
        "sort_by_source_asc": SearchArchiveIndexRequest(
            sortby="source", order="asc",
        ),
        "sort_by_source_desc": SearchArchiveIndexRequest(
            sortby="source", order="desc",
        ),
        # date_added (~100% coverage)
        "sort_by_date_added_asc": SearchArchiveIndexRequest(
            sortby="date_added", order="asc",
        ),
        "sort_by_date_added_desc": SearchArchiveIndexRequest(
            sortby="date_added", order="desc",
        ),

        # Deep pagination (stresses sort + offset)
        "deep_pagination_p50": SearchArchiveIndexRequest(
            start=str(num_archives // 2), sortby="title", order="asc",
        ),

        # Boolean filters
        "new_only": SearchArchiveIndexRequest(
            newonly=True,
        ),
        "untagged_only": SearchArchiveIndexRequest(
            untaggedonly=True,
        ),
    }

    for query_name, search_request in queries.items():
        for iteration in range(search_iterations):
            elapsed, count = await _timed_search(lrr_client, search_request)
            benchmark_collector.record(query_name, iteration, elapsed)
            LOGGER.info(
                f"  {query_name} iter={iteration}: {elapsed:.4f}s ({count} results)"
            )
    # <<<<< BENCHMARK QUERIES <<<<<
