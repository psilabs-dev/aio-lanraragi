"""
lrr-staging command: a deployment command line tool for LANraragi.

up: start a LANraragi environment. Docker-only (for now).
```sh
lrr-staging up \
    [--image $IMAGE] [--build $BUILD_PATH] [--git-url $GIT_URL] [--git-branch $GIT_BRANCH] [--docker-api]
```

down: tear down a LANraragi environment (including volumes and data)
```sh
lrr-staging down
```

restart: stop LRR, stop redis, start redis, start LRR.
```sh
lrr-staging restart
```

This command will deploy LANraragi on port offset +1:
- LANraragi: port 3001
- Redis: port 6380

The resource prefix for docker resources will be "staging_".
"""

import argparse
import logging
from typing import Optional

from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
import docker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')

    # up
    up_parser = subparsers.add_parser('up', help="Start a LRR deployment.")
    up_parser.add_argument('--image', type=str, required=False, help="Docker image to deploy LRR container on.")
    up_parser.add_argument('--build', type=str, required=False, help="Path to docker build context for LANraragi.")
    up_parser.add_argument('--git-url', type=str, required=False, help="URL of LANraragi git repository to build a Docker image from.")
    up_parser.add_argument('--git-branch', type=str, required=False, help="Branch name of the corresponding git repository.")
    up_parser.add_argument('--docker-api', action='store_true', required=False, help="Use Docker API client.")

    # down
    subparsers.add_parser('down', help="Stop a LRR deployment.")

    # restart
    subparsers.add_parser('restart', help="Restart a LRR deployment.")

    args = parser.parse_args()
    docker_client = docker.from_env()

    if args.command == 'up':
        image: Optional[str] = args.image
        build: Optional[str] = args.build
        git_url: Optional[str] = args.git_url
        git_branch: Optional[str] = args.git_branch
        docker_api = docker.APIClient(base_url="unix://var/run/docker.sock") if args.docker_api else None
        environment = DockerLRRDeploymentContext(
            build, image, git_url, git_branch, docker_client, docker_api=docker_api, logger=logger, global_run_id=1, is_allow_uploads=True
        )
        environment.setup("staging_", 1, with_api_key=True, with_nofunmode=True, lrr_debug_mode=True)

    elif args.command == 'down':
        environment = DockerLRRDeploymentContext(
            None, None, None, None, docker_client, docker_api=None, logger=logger, global_run_id=1, is_allow_uploads=True
        )
        environment.resource_prefix = "staging_"
        environment.port_offset = 1
        environment.teardown(remove_data=True)

    elif args.command == 'restart':
        environment = DockerLRRDeploymentContext(
            None, None, None, None, docker_client, docker_api=None, logger=logger, global_run_id=1, is_allow_uploads=True
        )
        environment.resource_prefix = "staging_"
        environment.port_offset = 1
        environment.restart()
    else:
        parser.print_help()

if __name__ == '__main__':
    main()