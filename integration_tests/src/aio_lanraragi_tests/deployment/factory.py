import logging
import os
import sys

import docker
import pytest

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.container import (
    ContainerLRRCacheBackend,
    ContainerLRRDeploymentContext,
    ContainerRuntime,
)
from aio_lanraragi_tests.deployment.homebrew import HomebrewLRRDeploymentContext
from aio_lanraragi_tests.deployment.windows import WindowsLRRDeploymentContext
from aio_lanraragi_tests.exceptions import DeploymentException


def _resolve_socket(runtime: ContainerRuntime, container_host: str | None) -> str | None:
    """
    Resolve the container runtime API socket URL. An explicit --container-host wins; otherwise
    rootless Podman uses its conventional per-user socket and Docker falls back to docker-py's
    environment discovery (honoring DOCKER_HOST). Returns None to signal "discover from environment".
    """
    if container_host:
        return container_host
    if runtime.is_rootless:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        return f"unix://{runtime_dir}/podman/podman.sock"
    return None


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
            # On macOS/Linux, --homebrew selects the native Homebrew deployment.
            build_path: str = request.config.getoption("--build")
            build_ref: str = request.config.getoption("--build-ref")
            image: str = request.config.getoption("--image")
            git_url: str = request.config.getoption("--git-url")
            git_ref: str = request.config.getoption("--git-ref")
            dockerfile: str = request.config.getoption("--dockerfile")
            use_docker_api: bool = request.config.getoption("--docker-api")
            staging_dir: str = request.config.getoption("--staging")
            cache_backend: str = request.config.getoption("--cache-backend")

            if request.config.getoption("--homebrew"):
                windist: str = request.config.getoption("--windist")
                if not build_path:
                    raise DeploymentException("--homebrew requires --build <path to local LANraragi source>.")
                if image or git_url or dockerfile or windist:
                    raise DeploymentException("--homebrew cannot be combined with --image, --git-url, --dockerfile, or --windist.")
                if cache_backend == "valkey8":
                    raise DeploymentException(
                        "--homebrew does not support --cache-backend valkey8; the Homebrew formula provides redis. Use --cache-backend redis."
                    )
                cache_backend = "redis"
                environment = HomebrewLRRDeploymentContext(
                    build_path, build_ref, staging_dir, resource_prefix, port_offset, cache_backend,
                    logger=logger,
                )
            else:
                container_runtime = ContainerRuntime(request.config.getoption("--container-runtime"))
                container_host: str = request.config.getoption("--container-host")
                if dockerfile and git_url:
                    raise DeploymentException("--dockerfile cannot be combined with --git-url.")
                if dockerfile and image:
                    raise DeploymentException("--dockerfile cannot be combined with --image.")

                # Resolve a single socket for the runtime, then derive both the high-level client and the
                # low-level API client from it so they always target the same endpoint.
                socket_url = _resolve_socket(container_runtime, container_host)
                docker_client = docker.DockerClient(base_url=socket_url) if socket_url else docker.from_env()
                docker_api = docker_client.api if use_docker_api else None
                environment = ContainerLRRDeploymentContext(
                    build_path, image, git_url, git_ref, docker_client, staging_dir, resource_prefix, port_offset,
                    build_ref=build_ref, dockerfile=dockerfile, docker_api=docker_api,
                    global_run_id=global_run_id, is_allow_uploads=True,
                    logger=logger,
                    cache_backend=ContainerLRRCacheBackend(cache_backend),
                    container_runtime=container_runtime,
                )

    return environment
