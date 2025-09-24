import logging
import sys
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.deployment.windows import WindowsLRRDeploymentContext
import docker
import pytest

def generate_deployment(
    request: pytest.FixtureRequest, resource_prefix: str, port_offset: int,
    logger: logging.Logger=None
) -> AbstractLRRDeploymentContext:
    """
    Create and return an appropriate, uninitialized deployment according to the pytest request arguments.
    """
    global_run_id: int = request.config.global_run_id
    environment: AbstractLRRDeploymentContext = None

    # check operating system.
    match sys.platform:
        case 'win32':
            windist: str = request.config.getoption("--windist")
            staging_dir: str = request.config.getoption("--staging")
            environment = WindowsLRRDeploymentContext(
                windist, staging_dir, resource_prefix, port_offset,
                logger=logger
            )

        case 'darwin' | 'linux':
            # TODO: we're assuming macos is used as a development environment with docker installed,
            # not a testing environment; for macos github runners, we would be using them
            # to run homebrew integration tests.

            build_path: str = request.config.getoption("--build")
            image: str = request.config.getoption("--image")
            git_url: str = request.config.getoption("--git-url")
            git_branch: str = request.config.getoption("--git-branch")
            use_docker_api: bool = request.config.getoption("--docker-api")
            docker_client = docker.from_env()
            docker_api = docker.APIClient(base_url="unix://var/run/docker.sock") if use_docker_api else None
            environment = DockerLRRDeploymentContext(
                build_path, image, git_url, git_branch, docker_client, resource_prefix, port_offset, docker_api=docker_api,
                global_run_id=global_run_id, is_allow_uploads=True,
                logger=logger
            )

    return environment
