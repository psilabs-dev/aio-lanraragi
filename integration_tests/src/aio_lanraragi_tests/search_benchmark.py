"""
Search benchmarking utilities and command line.

The benchmark will run in the following stages, where each stage requires the previous stage.

1. deployment setup stage
2. archive cleanup stage
2. archive create stage
3. archive upload stage
4. search benchmark stage
5. deployment teardown stage

The LRR/Redis deployment will have port offset 2, with resource prefix "benchmark_search_".
"""

import asyncio
from http import HTTPMethod
import json
import logging
import math
from pathlib import Path
import time
import aiofiles
import docker
import numpy as np
import shutil
import sys
from typing import Dict, List, Optional

from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.metadata.zipf_utils import get_archive_idx_to_tag_idxs_map
from aio_lanraragi_tests.archive_generation.models import CreatePageRequest, WriteArchiveRequest, WriteArchiveResponse
from aio_lanraragi_tests.common import DEFAULT_API_KEY, compute_archive_id
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.helpers import upload_archive

from lanraragi.clients.client import LRRClient
from lanraragi.models.minion import GetMinionJobStatusRequest

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DEFAULT_STAGING_DIR = str(Path.cwd() / ".staging")
DEFAULT_RESOURCE_PREFIX = "benchmark_search_"
DEFAULT_PORT_OFFSET = 2
DEFAULT_NUM_ARCHIVES = 500_000
DEFAULT_NUM_TAGS = 100_000
DEFAULT_ARTISTS = 1000

def __get_synthetic_data_dir(staging_dir: str):
    ".staging/benchmark_search_synthetic_data/"
    return Path(staging_dir) / f"{DEFAULT_RESOURCE_PREFIX}synthetic_data"

def get_deployment(
    build_path: str=None, image: str=None, git_url: str=None, git_branch: str=None, docker_api: docker.APIClient=None, staging_dir: str=None
) -> DockerLRRDeploymentContext:
    docker_client = docker.from_env()
    environment = DockerLRRDeploymentContext(
        build_path, image, git_url, git_branch, docker_client, staging_dir, DEFAULT_RESOURCE_PREFIX, DEFAULT_PORT_OFFSET, docker_api=docker_api,
        global_run_id=0, is_allow_uploads=True, is_force_build=True
    )
    return environment

def require_up(staging_dir: str) -> bool:
    d = get_deployment(staging_dir=staging_dir)
    try:
        d.test_lrr_connection(d.lrr_port, test_connection_max_retries=1)
        return True
    except DeploymentException:
        return False

def up(
    image: str=None, git_url: str=None, git_branch: str=None, build: str=None, docker_api: docker.APIClient=None, staging_dir: str=None,
    with_nofunmode: bool=False
):
    # increase number of retries since LRR is going to be busy with archive discovery.
    d = get_deployment(build_path=build, image=image, git_url=git_url, git_branch=git_branch, docker_api=docker_api, staging_dir=staging_dir)
    d.setup(with_api_key=True, with_nofunmode=with_nofunmode, test_connection_max_retries=8)
    print("LRR benchmarking environment setup complete.")

    if require_up(staging_dir):
        sys.exit(0)
    else:
        print("Failed to ensure LRR instance connected.")
        sys.exit(1)

def clean(staging_dir: str=None):
    generated_data_dir = __get_synthetic_data_dir(staging_dir)
    if generated_data_dir.exists():
        shutil.rmtree(generated_data_dir)
        print("Removed all generated data.")
    else:
        print("No generated data to remove.")
    sys.exit(0)

def require_generate(staging_dir: str=None) -> bool:
    archives_dir = __get_synthetic_data_dir(staging_dir) / "archives"
    generate_result_path = __get_synthetic_data_dir(staging_dir) / "generated_data.json"

    if not archives_dir.exists():
        LOGGER.warning(f"No synthetic archives directory: {archives_dir}")
        return False
    
    if not generate_result_path.exists():
        LOGGER.warning(f"No generated data manifest: {generate_result_path}")
        return False
    
    with open(generate_result_path, 'r') as f:
        data = json.load(f)

    for archive in data["archives"]:
        path = Path(archive['path'])
        if not path.exists():
            LOGGER.warning(f"Archive does not exist: {path}")
            return False
    
    return True

def generate(staging_dir: str=None, num_archives: int=None, num_tags: int=None, num_artists: int=None):
    print("Generating synthetic data for search benchmark...")
    np_generator = np.random.default_rng(42)

    archives_dir = __get_synthetic_data_dir(staging_dir) / "archives"
    generate_result_path = __get_synthetic_data_dir(staging_dir) / "generated_data.json"
    if generate_result_path.exists():
        print("Found generated_data.json: validating...")
        if require_generate(staging_dir=staging_dir):
            print("Validation passed: skipping generation.")
            sys.exit(0)
        else:
            print("Validation failed! Run `clean` and try again.")
            sys.exit(1)
    elif archives_dir.exists(): # no generated JSON path, but archives? incomplete.
        print("Found archives directory but no generated JSON! Check the data.")
        sys.exit(1)

    archives_dir.mkdir(parents=True, exist_ok=True)
    requests: List[WriteArchiveRequest] = []
    responses: List[WriteArchiveResponse] = []

    # ensure sharded subdirectories with up to 1,000 archives each
    num_subdirs = (num_archives - 1) // 1000 + 1
    subdir_digits = len(str(max(0, num_subdirs - 1)))
    for archive_id in range(num_archives):
        create_page_requests = []
        archive_name = f"archive-{str(archive_id+1).zfill(len(str(num_archives)))}"
        filename = f"{archive_name}.zip"
        subdir_name = str(archive_id // 1000).zfill(subdir_digits or 1)
        subdir_path = archives_dir / subdir_name
        subdir_path.mkdir(parents=True, exist_ok=True)
        save_path = subdir_path / filename
        num_pages = np_generator.integers(1, 2)
        for page_id in range(num_pages):
            page_text = f"{archive_name}-pg-{str(page_id+1).zfill(len(str(num_pages)))}"
            page_filename = f"{page_text}.png"
            create_page_request = CreatePageRequest(
                width=144, height=144, filename=page_filename, image_format='PNG', text=page_text
            )
            create_page_requests.append(create_page_request)        
        requests.append(WriteArchiveRequest(create_page_requests=create_page_requests, save_path=save_path, archival_strategy=ArchivalStrategyEnum.ZIP))
    responses = write_archives_to_disk(requests)

    # generate zipf tag distribution.
    archive_idx_to_tag_idx_list = get_archive_idx_to_tag_idxs_map(num_archives, num_tags, 20, np_generator)
    archive_idx_to_artist_idx_list = get_archive_idx_to_tag_idxs_map(num_archives, num_artists, 1, np_generator)

    arcidx_to_arcid: Dict[int, str] = {}
    for arcidx, response in enumerate(responses):
        save_path = response.save_path
        arcid = compute_archive_id(save_path)
        arcidx_to_arcid[arcidx] = arcid

    tag_idx_to_tag_id: Dict[int, str] = {idx: f"tag-{idx}" for idx in range(num_tags)}
    artist_idx_to_artist_id: Dict[int, str] = {artistidx: f"artist:artist-{artistidx}" for artistidx in range(num_artists)}

    data = {
        'archives': [{
            'path': str(response.save_path),
            'tag_list': [tag_idx_to_tag_id[idx] for idx in archive_idx_to_tag_idx_list[arcidx]] + [artist_idx_to_artist_id[idx] for idx in archive_idx_to_artist_idx_list[arcidx]],
            'title': f"title-{arcidx}",
            'arcid': arcidx_to_arcid[arcidx],
        } for arcidx, response in enumerate(responses)]
    }

    with open(generate_result_path, 'w') as f:
        json.dump(
            data, f,
            # indent=4
        )

    if require_generate(staging_dir=staging_dir):
        print("Data generation validation passed.")
        sys.exit(0)
    else:
        print("Data generation validation failed.")
        sys.exit(1)

async def require_upload(lrr_client: LRRClient, archives) -> bool:
    """
    Validate the uploads have gone through.
    """
    arcid_to_archive_local = {
        archive["arcid"]: archive for archive in archives
    }

    num_archives = len(archives)

    response, error = await lrr_client.archive_api.get_all_archives()
    if error:
        LOGGER.error(f"Failed to get all archives from LRR: {error.error}")
        return False

    num_archives_in_lrr = len(response.data)
    if num_archives_in_lrr != num_archives:
        LOGGER.error(f"Number of archives in LRR {num_archives_in_lrr} does not match {num_archives}.")
        return False
    
    for archive in response.data:
        if archive.arcid not in arcid_to_archive_local:
            LOGGER.error(f"Archive {archive.arcid} not found locally.")
            return False
        local_archive = arcid_to_archive_local[archive.arcid]
        title = local_archive["title"]
        tag_list = local_archive["tag_list"]
        if archive.title != title:
            LOGGER.error(f"Local title \"{title}\" != \"{archive.title}\"")
            return False
        if not set(archive.tags.split(',')).issuperset(set(tag_list)):
            LOGGER.error(f"Local archive includes tags not in LRR: {','.join(tag_list)} (remote: {archive.tags})")
            return False
    
    return True

async def upload(staging_dir: str):
    print("Uploading synthetic data to benchmarking instance...")
    if not require_up(staging_dir):
        print("Staging environment not established.")
        sys.exit(1)
    if not require_generate(staging_dir):
        print("Data generation prerequisites not met.")
        sys.exit(1)

    d = get_deployment(staging_dir=staging_dir)
    lrr_client = d.lrr_client() # default creds are used so we don't need to care about configuration
    lrr_client.update_api_key(DEFAULT_API_KEY)
    generate_result_path = __get_synthetic_data_dir(staging_dir) / "generated_data.json"
    async with aiofiles.open(generate_result_path, 'r') as f:
        data = json.loads(await f.read())

    archives = data["archives"]
    batch_size = 1000

    # batch processing with time estimation
    # get some coffee... and maybe lunch.
    try:
        print("Uploading archives...")
        upload_start_time = time.time()

        sem = asyncio.BoundedSemaphore(value=4)
        avg_batch_times: List[float] = []
        total_batches = math.ceil(len(archives) / batch_size)

        for batch_idx, i in enumerate(range(0, len(archives), batch_size), start=1):
            batch_start_time = time.time()
            batch = archives[i:i + batch_size]

            tasks = []
            for archive in batch:
                save_path = Path(archive["path"])
                filename = save_path.name
                title = archive["title"]
                tag_list = archive["tag_list"]
                tags = ",".join(tag_list)

                # if duplicates exist or internal server errors happen: skip them
                tasks.append(asyncio.create_task(
                    upload_archive(
                        lrr_client,
                        save_path,
                        filename,
                        sem,
                        title=title,
                        tags=tags,
                        allow_duplicates=True,
                        retry_on_ise=True,
                    )
                ))
            await asyncio.gather(*tasks)
            batch_time = time.time() - batch_start_time
            avg_batch_times.append(batch_time)
            avg_batch_time = sum(avg_batch_times[-10:]) / len(avg_batch_times[-10:]) # ignore skipped archives.

            remaining_batches = total_batches - batch_idx
            eta_seconds = remaining_batches * avg_batch_time
            print(
                f"Finished batch {batch_idx}/{total_batches} in {batch_time:.2f}s; "
                f"estimated time remaining: {eta_seconds:.1f}s (~{eta_seconds/60:.1f}m)"
            )

        upload_time = time.time() - upload_start_time
        print(f"All archives uploaded after {upload_time:.2f}s; checking archive count...")

        status, content = await lrr_client.handle_request(
            HTTPMethod.POST,
            lrr_client.build_url("/api/minion/build_stat_hashes/queue"),
            lrr_client.headers,
            data={
                "args": "[]",
                "priority": "3"
            }
        )
        if status != 200:
            print(f"Failed to queue build_stat_hashes: {content}")
            sys.exit(1)
        build_stat_hashes_data = json.loads(content)
        job_id = int(build_stat_hashes_data["job"])

        print("Waiting for stat hashes to build...")
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                print("Failed to finish build_stat_hashes after 60s.")
                sys.exit()
            response, error = await lrr_client.minion_api.get_minion_job_status(GetMinionJobStatusRequest(
                job_id=job_id
            ))
            if error:
                print(f"Failed to get job status: {error.error}")
                sys.exit(1)
            state = (response.state).lower()
            if state == "finished":
                break
            elif state == "failed":
                print("build_stat_hashes job failed.")
                sys.exit(1)
            elif state == "active" or state == "inactive":
                await asyncio.sleep(1)
            else:
                print(f"Unknown build_stat_hashes state: {state}")
                sys.exit(1)

        if not await require_upload(lrr_client, archives):
            print("Failed upload validation.")
            sys.exit(1)
        else:
            print("Upload validation OK.")
    finally:
        await lrr_client.close()

def bench(staging_dir: str):
    # TODO: the meat of the program
    print("Running benchmark...")
    if not require_up(staging_dir):
        print("Staging environment not established.")
        sys.exit(1)
    if not require_generate(staging_dir):
        print("Data generation prerequisites not met.")
        sys.exit(1)

def down(staging_dir: str=None):
    d = get_deployment(staging_dir=staging_dir)
    d.teardown(remove_data=True)
    print("LRR benchmarking environment teardown complete.")
    sys.exit(0)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    setup_sp = subparsers.add_parser('up', help='Setup the LRR benchmark application.')
    setup_sp.add_argument("--image", help="Docker image to use")
    setup_sp.add_argument("--git-url", help="Git URL to use")
    setup_sp.add_argument("--git-branch", help="Git branch to use")
    setup_sp.add_argument("--build", help="Build path to use")
    setup_sp.add_argument("--docker-api", action='store_true', help="Stream docker build logs")
    setup_sp.add_argument("--nofunmode", action="store_true", help="Start LRR with nofunmode (default false).")
    setup_sp.add_argument("--staging", default=DEFAULT_STAGING_DIR, help="Path to staging directory.")

    cleanup_sp = subparsers.add_parser('clean', help='Cleanup the locally created archives.')
    cleanup_sp.add_argument('--staging', default=DEFAULT_STAGING_DIR, help='Path to staging directory.')

    generate_sp = subparsers.add_parser('generate', help='Generate the archives and metadata.')
    generate_sp.add_argument('--staging', default=DEFAULT_STAGING_DIR, help='Path to staging directory.')
    generate_sp.add_argument('--archives', type=int, default=DEFAULT_NUM_ARCHIVES, help=f'Number of total archives to generate (default {DEFAULT_NUM_ARCHIVES})')
    generate_sp.add_argument('--tags', type=int, default=DEFAULT_NUM_TAGS, help=f'Number of total tags to generate (default {DEFAULT_NUM_TAGS})')
    generate_sp.add_argument('--artists', type=int, default=DEFAULT_ARTISTS, help=f'Number of total artists to generate (default {DEFAULT_ARTISTS})')
    
    upload_sp = subparsers.add_parser('upload', help='Upload archives to benchmarked instance.')
    upload_sp.add_argument('--staging', default=DEFAULT_STAGING_DIR, help='Path to staging directory.')
    
    benchmark_sp = subparsers.add_parser('bench', help='Run the benchmark.')
    benchmark_sp.add_argument('--staging', default=DEFAULT_STAGING_DIR, help='Path to staging directory.')
    
    teardown_sp = subparsers.add_parser('down', help='Clean everything up.')
    teardown_sp.add_argument('--staging', default=DEFAULT_STAGING_DIR, help='Path to staging directory.')
    args = parser.parse_args()

    try:
        match args.command:
            case 'up':
                docker_api: Optional[docker.APIClient] = None
                if args.docker_api:
                    docker_api = docker.APIClient(base_url="unix://var/run/docker.sock")
                up(
                    image=args.image, git_url=args.git_url, git_branch=args.git_branch, build=args.build, docker_api=docker_api, staging_dir=args.staging,
                    with_nofunmode=args.nofunmode
                )
            case 'clean':
                clean(staging_dir=args.staging)
            case 'generate':
                generate(staging_dir=args.staging, num_archives=args.archives, num_tags=args.tags, num_artists=args.artists)
            case 'upload':
                asyncio.run(upload(staging_dir=args.staging))
            case 'bench':
                bench(staging_dir=args.staging)
            case 'down':
                down(staging_dir=args.staging)
            case _:
                parser.print_help()
                sys.exit(1)
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(130)
