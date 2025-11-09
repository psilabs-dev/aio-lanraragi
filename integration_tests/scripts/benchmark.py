"""
1M archive search algorithm relative benchmarking and fuzzing script for LRR.
"""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import re
import sys
import time
from typing import Dict, List, Set
from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.models import CreatePageRequest, WriteArchiveRequest, WriteArchiveResponse
import numpy as np
import aiofiles

from lanraragi.clients.client import LRRClient
from aio_lanraragi_tests.helpers import upload_archive
from aio_lanraragi_tests.common import compute_upload_checksum

LOGGER = logging.getLogger(__name__)

# sanitize the text according to the search syntax: https://sugoi.gitbook.io/lanraragi/basic-operations/searching
def sanitize_tag(text: str) -> str:
    sanitized = text
    # replace nonseparator characters with empty str. (", ?, *, %, $, :)
    sanitized = re.sub(r'["?*%$:]', '', sanitized)
    # replace underscore with space.
    sanitized = sanitized.replace('_', ' ')
    # if a dash is preceded by space, remove; otherwise, keep.
    sanitized = sanitized.replace(' -', ' ')
    if sanitized != text:
        LOGGER.debug(f"\"{text}\" was sanitized.")
    return sanitized

def get_data_dir():
    return Path(__file__).parent.parent / ".benchmark"

def get_archives_dir():
    return get_data_dir() / "archives"

def get_metadata_json_file():
    return get_data_dir() / "metadata.json"

def generate_tag(np_generator: np.random.Generator) -> str:
    """
    Generate a random-length (1..99 inclusive) Unicode string from all valid
    Unicode scalar values (U+0000..U+10FFFF), excluding surrogates.
    """
    tag_length: int = int(np_generator.integers(1, 100))
    cps = np_generator.integers(0, 0x110000, size=tag_length, dtype=np.uint32)
    surrogate_mask = (cps >= 0xD800) & (cps <= 0xDFFF)
    while surrogate_mask.any():
        cps[surrogate_mask] = np_generator.integers(
            0, 0x110000, size=int(surrogate_mask.sum()), dtype=np.uint32
        )
        surrogate_mask = (cps >= 0xD800) & (cps <= 0xDFFF)
    tag = ''.join(map(chr, cps.tolist()))
    tag = sanitize_tag(tag)
    return tag

def save_archives(num_archives: int, work_dir: Path, np_generator: np.random.Generator) -> List[WriteArchiveResponse]:
    """
    Each archive will be 1-2 pages (for space), and be 144x144 in res.
    Write will be performed with multiprocessing.
    """
    requests = []
    responses = []
    # ensure sharded subdirectories with up to 1,000 archives each
    num_subdirs = (num_archives - 1) // 1000 + 1
    subdir_digits = len(str(max(0, num_subdirs - 1)))
    for archive_id in range(num_archives):
        create_page_requests = []
        archive_name = f"archive-{str(archive_id+1).zfill(len(str(num_archives)))}"
        filename = f"{archive_name}.zip"
        subdir_name = str(archive_id // 1000).zfill(subdir_digits or 1)
        subdir_path = work_dir / subdir_name
        subdir_path.mkdir(parents=True, exist_ok=True)
        save_path = subdir_path / filename
        num_pages = np_generator.integers(1, 2)
        for page_id in range(num_pages):
            page_text = f"{archive_name}-pg-{str(page_id+1).zfill(len(str(num_pages)))}"
            page_filename = f"{page_text}.png"
            create_page_request = CreatePageRequest(144, 144, page_filename, image_format='PNG', text=page_text)
            create_page_requests.append(create_page_request)        
        requests.append(WriteArchiveRequest(create_page_requests, save_path, ArchivalStrategyEnum.ZIP))
    responses = write_archives_to_disk(requests)
    return responses

async def benchmark():
    ...

def generate_archives(
    num_archives: int,
    num_tags: int,
    num_artists: int,
    rng_seed: int,
    poisson_lam: float = 7.0,
    tag_zipf_exp: float = 1.1,
    artist_zipf_exp: float = 1.2,
):
    """
    Writes all archives to disk, and creates an accompanying metadata.json file.

    Tag popularity ~ Zipf(tag_zipf_exp).
    Artist productivity (images per artist) ~ Zipf(artist_zipf_exp).
    """

    np_generator = np.random.default_rng(rng_seed)

    archives_dir = get_archives_dir()
    archives_dir.mkdir(parents=True, exist_ok=True)
    responses = save_archives(num_archives, archives_dir, np_generator)
    LOGGER.info(f"Wrote {num_archives} archives to disk.")

    # Tags: mock tag IDs using Zipf
    image_id_to_tag_ids: Dict[int, List[int]] = {}
    for image_id in range(num_archives):
        k = np_generator.poisson(lam=poisson_lam)
        k = max(1, min(k, 15, num_tags))

        chosen: Set[int] = set()
        while len(chosen) < k:
            samples = np_generator.zipf(tag_zipf_exp, size=k * 2)
            for r in samples:
                idx = int(r - 1)
                if 0 <= idx < num_tags:
                    chosen.add(idx)
                    if len(chosen) == k:
                        break

        tag_ids = sorted(chosen)
        image_id_to_tag_ids[image_id] = tag_ids

    # now create mock tag strings instead of predictable integer tags.
    tag_id_to_tag: Dict[int, str] = {}
    tag_pool: Set[str] = set()  # keep track of and avoid duplicate tags.
    for tag_id in range(num_tags):
        while True:
            tag = generate_tag(np_generator).strip()
            if not tag:
                continue
            if tag in tag_pool:
                continue
            tag_pool.add(tag)
            tag_id_to_tag[tag_id] = tag
            break

    # switch archive->tag ID map to archive->tag strings
    image_id_to_tags: Dict[int, List[str]] = {}
    for image_id, tag_ids in image_id_to_tag_ids.items():
        tags = [tag_id_to_tag[tag_id] for tag_id in tag_ids]
        image_id_to_tags[image_id] = tags

    # Artists: Zipf-like productivity
    image_id_to_artist_id: Dict[int, int] = {}

    # assign each image an artist ID using a truncated Zipf over artist ranks
    for image_id in range(num_archives):
        while True:
            r = int(np_generator.zipf(artist_zipf_exp))  # ranks: 1,2,...
            idx = r - 1  # convert to 0-based artist index
            if 0 <= idx < num_artists:
                image_id_to_artist_id[image_id] = idx
                break

    # now create mock artist strings
    artist_id_to_artist: Dict[int, str] = {
        artist_id: f"artist-{artist_id}" for artist_id in range(num_artists)
    }

    # switch archive->artist ID map to archive->artist strings
    image_id_to_artist: Dict[int, str] = {}
    for image_id, artist_id in image_id_to_artist_id.items():
        image_id_to_artist[image_id] = artist_id_to_artist[artist_id]

    # Other metadata (titles, dates, source)
    image_id_to_title: Dict[int, str] = {}
    for image_id in range(num_archives):
        image_id_to_title[image_id] = f"archive-{image_id}"  # TODO: fancy titles later

    image_id_to_date_created: Dict[int, str] = {}
    image_id_to_source: Dict[int, str] = {}

    start_epoch = int(np.datetime64('2020-01-01').astype('datetime64[s]').astype(int))
    end_epoch = int(np.datetime64('2025-01-01').astype('datetime64[s]').astype(int))

    for image_id in range(num_archives):
        image_id_to_source[image_id] = f"https://www.lrr_data_source.com/id/{image_id}"
        image_id_to_date_created[image_id] = str(
            np_generator.integers(start_epoch, end_epoch)
        )

    # Dump metadata to JSON
    metadata_path = get_metadata_json_file()
    save_path_to_metadata = {}
    for image_id, response in enumerate(responses):
        save_path = response.save_path
        tag_list = (
            image_id_to_tags[image_id]
            + [f"artist:{image_id_to_artist[image_id]}"]
            + [f"date_created:{image_id_to_date_created[image_id]}"]
            + [f"source:{image_id_to_source[image_id]}"]
        )
        save_path_to_metadata[Path(save_path).name] = {
            "tag_list": tag_list,
            "title": image_id_to_title[image_id],
        }

    with open(metadata_path, "w") as f:
        json.dump(save_path_to_metadata, f)

    LOGGER.info(f"Wrote metadata for {num_archives} archives to {metadata_path}")

async def upload_archives(lrr_address: str):
    """
    Upload all archives from the benchmark data directory using metadata.json.
    """
    start = time.time()
    data_dir = get_data_dir()
    archives_dir = get_archives_dir()
    metadata_path = get_metadata_json_file()
    api_key = "lanraragi"

    if not data_dir.exists() or not archives_dir.exists() or not metadata_path.exists():
        print(f"Missing benchmark data. Expected directories/files:\n - {data_dir}\n - {archives_dir}\n - {metadata_path}")
        sys.exit(1)

    async with aiofiles.open(metadata_path, "r") as f:
        metadata: Dict[str, Dict[str, object]] = json.loads(await f.read())

    base_url = lrr_address
    concurrency = 1
    max_retries = 4

    semaphore = asyncio.BoundedSemaphore(value=concurrency)
    async def upload_one(client: LRRClient, file_path: Path, title: str, tags_csv: str) -> tuple[bool, str]:
        filename = file_path.name
        checksum = compute_upload_checksum(file_path)
        response, error = await upload_archive(
            client=client,
            save_path=file_path,
            filename=filename,
            semaphore=semaphore,
            checksum=checksum,
            title=title,
            tags=tags_csv,
            max_retries=max_retries
        )
        assert not error, f"Failed to upload archive {file_path}: {error.error}"
        LOGGER.info(f"[upload_archives] uploaded {filename} -> {response.arcid}")
        return True, filename

    # include archives in subdirectories as well
    files = sorted([p for p in archives_dir.rglob("*.zip") if p.is_file()])
    if not files:
        LOGGER.info("No archives found to upload.")
        return

    async with LRRClient(lrr_base_url=base_url, lrr_api_key=api_key) as lrr_client:
        tasks: List[asyncio.Task] = []
        for file_path in files:
            meta = metadata[file_path.name]
            title = str(meta["title"])
            tag_list = meta["tag_list"]
            tags_csv = ",".join(tag_list)
            tasks.append(asyncio.create_task(upload_one(lrr_client, file_path, title, tags_csv)))

        await asyncio.gather(*tasks)
    
    end = time.time()
    LOGGER.info(f"Done uploading archives after {end-start}s.")

async def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    generate_subparser = subparsers.add_parser("generate", help="Generate archive and metadata info.")
    generate_subparser.add_argument("--archives", type=int, default=1_000_000, help="Number of archives to use for benchmarking.")
    generate_subparser.add_argument("--artists", type=int, default=50_000, help="Number of artists in the artist namespace.")
    generate_subparser.add_argument("--tags", type=int, default=200_000, help="Number of tags to use for benchmarking.")
    generate_subparser.add_argument("--seed", type=int, default=42, help="RNG seed to pass.")

    upload_subparser = subparsers.add_parser("upload", help="Upload all archives to LRR with metadata.")
    upload_subparser.add_argument("--lrr-address", type=str, default="http://127.0.0.1:3001", help="Base URL for the LRR instance")
    subparsers.add_parser("benchmark", help="Run benchmark of LRR.")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    try:
        match args.command:
            case "generate":
                if not get_data_dir().exists():
                    print(f"Benchmark directory does not exist: {get_data_dir()}")
                    sys.exit(1)
                num_archives: int = args.archives
                num_tags: int = args.tags
                num_artists: int = args.artists
                rng_seed: int = args.seed
                generate_archives(num_archives, num_tags, num_artists, rng_seed)

            case "upload":
                if not get_data_dir().exists():
                    print(f"Benchmark directory does not exist: {get_data_dir()}")
                    sys.exit(1)
                await upload_archives(args.lrr_address)

            case "benchmark":
                await benchmark()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(130)

if __name__ == "__main__":
    asyncio.run(main())
