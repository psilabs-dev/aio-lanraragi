"""
`lrr-staging` development command line tools.
"""

import argparse
import logging
import os
from pathlib import Path
import sys
from typing import Optional
import docker

from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.utils import get_version

STAGING_RESOURCE_PREFIX = "staging_"
STAGING_PORT_OFFSET = 1

LOGGER = logging.getLogger(__name__)
DEFAULT_STAGING_DIR = str(Path.cwd() / ".staging")

def get_deployment(
    build_path: str=None, image: str=None, git_url: str=None, git_branch: str=None, docker_api: docker.APIClient=None, staging_dir: str=None
) -> DockerLRRDeploymentContext:
    """
    Get docker deployment context.

    Arguments are optional: resources are uniquely determined by resource prefix and port offset,
    which are fixed at this point in time. Regardless of *how* the container was created, it will
    always be mapped to the same staging environment identity.

    Naturally, docker installation is required.
    """

    docker_client = docker.from_env()
    environment = DockerLRRDeploymentContext(
        build_path, image, git_url, git_branch, docker_client, staging_dir, STAGING_RESOURCE_PREFIX, STAGING_PORT_OFFSET, docker_api=docker_api,
        global_run_id=0, is_allow_uploads=True, is_force_build=True
    )
    return environment

def up(
    image: str=None, git_url: str=None, git_branch: str=None, build: str=None, docker_api: docker.APIClient=None, staging_dir: str=None,
    with_nofunmode: bool=False
):
    d = get_deployment(build_path=build, image=image, git_url=git_url, git_branch=git_branch, docker_api=docker_api, staging_dir=staging_dir)
    d.setup(with_api_key=True, with_nofunmode=with_nofunmode)
    print("LRR staging environment setup complete.")
    sys.exit(0)

def down(remove_data: bool=False, staging_dir: str=None):
    d = get_deployment(staging_dir=staging_dir)
    d.teardown(remove_data=remove_data)
    print("LRR staging environment teardown complete.")
    sys.exit(0)

def restart(staging_dir: str=None):
    d = get_deployment(staging_dir=staging_dir)
    d.restart()
    print("LRR staging environment restarted.")
    sys.exit(0)

def stop(staging_dir: str=None):
    d = get_deployment(staging_dir=staging_dir)
    d.stop()
    print("LRR staging environment stopped.")
    sys.exit(0)

def start(staging_dir: str=None):
    d = get_deployment(staging_dir=staging_dir)
    d.start()
    print("LRR staging environment started.")
    sys.exit(0)

def console():
    parser = argparse.ArgumentParser(prog="lrr-staging", description="LRR staging environment command line helper utilities.")

    subparsers = parser.add_subparsers(dest="command")
    up_parser = subparsers.add_parser("up", help="Create and start staging environment")
    up_parser.add_argument("--image", help="Docker image to use")
    up_parser.add_argument("--git-url", help="Git URL to use")
    up_parser.add_argument("--git-branch", help="Git branch to use")
    up_parser.add_argument("--build", help="Build path to use")
    up_parser.add_argument("--docker-api", action='store_true', help="Stream docker build logs")
    up_parser.add_argument("--nofunmode", action="store_true", help="Start LRR with nofunmode (default false).")
    up_parser.add_argument("--staging", default=DEFAULT_STAGING_DIR, help="Path to staging directory.")

    down_parser = subparsers.add_parser("down", help="Teardown services")
    down_parser.add_argument("--volumes", action="store_true", help="Remove volumes")
    down_parser.add_argument("--staging", default=DEFAULT_STAGING_DIR, help="Path to staging directory.")

    restart_parser = subparsers.add_parser("restart", help="Restart environment (requires created)")
    restart_parser.add_argument("--staging", default=DEFAULT_STAGING_DIR, help="Path to staging directory.")

    stop_parser = subparsers.add_parser("stop", help="Stop environment (requires created)")
    stop_parser.add_argument("--staging", default=DEFAULT_STAGING_DIR, help="Path to staging directory.")

    start_parser = subparsers.add_parser("start", help="Start environment (requires created)")
    start_parser.add_argument("--staging", default=DEFAULT_STAGING_DIR, help="Path to staging directory.")

    subparsers.add_parser("version", help="Get integration tests version")

    if log_level := os.getenv("LOGLEVEL"):
        log_level = log_level.upper()
        if log_level not in {"INFO", "DEBUG", "WARNING", "ERROR"}:
            print(f"Invalid log level: {log_level}.")
            sys.exit(1)
    else:
        log_level = "WARNING"
    logging.basicConfig(level=log_level)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        match args.command:
            case "up":
                docker_api: Optional[docker.APIClient] = None
                if args.docker_api:
                    docker_api = docker.APIClient(base_url="unix://var/run/docker.sock")
                up(
                    image=args.image, git_url=args.git_url, git_branch=args.git_branch, build=args.build, docker_api=docker_api, staging_dir=args.staging,
                    with_nofunmode=args.nofunmode
                )
            case "down":
                down(remove_data=args.volumes, staging_dir=args.staging)
            case "restart":
                restart(staging_dir=args.staging)
            case "stop":
                stop(staging_dir=args.staging)
            case "start":
                start(staging_dir=args.staging)
            case "version":
                print(get_version())
                sys.exit(0)
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(130)
