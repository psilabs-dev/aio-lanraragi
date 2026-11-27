import contextlib
import logging
import os
import sys
from pathlib import Path

import docker
import pytest

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.container import (
    ContainerLRRCacheBackend,
    ContainerLRRDeploymentContext,
    ContainerRuntime,
)
from aio_lanraragi_tests.deployment.homebrew import (
    HomebrewBuild,
    HomebrewLRRDeploymentContext,
    install_homebrew_build,
)
from aio_lanraragi_tests.deployment.windows import WindowsLRRDeploymentContext
from aio_lanraragi_tests.exceptions import DeploymentException

_HOMEBREW_BUILD = pytest.StashKey[HomebrewBuild | str]()


def _resolve_socket(runtime: ContainerRuntime, container_url: str | None) -> str | None:
    """
    Resolve the container runtime API socket URL: --container-url if given, else the rootless
    per-user Podman socket on Linux for podman. Returns None for docker, deferring to docker-py's
    `from_env` (https://docker-py.readthedocs.io/en/stable/client.html#docker.client.from_env).
    """
    if container_url:
        return container_url
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

    if request.config.getoption("--homebrew"):
        if sys.platform != "darwin":
            raise DeploymentException("--homebrew is supported on macOS.")
        build_path: str | None = request.config.getoption("--build")
        if not build_path:
            raise DeploymentException("--homebrew requires --build <path to local LANraragi source>.")
        for option in ("--image", "--git-url", "--git-ref", "--dockerfile", "--windist", "--container-url", "--docker-api"):
            if request.config.getoption(option):
                raise DeploymentException(f"--homebrew cannot be combined with {option}.")
        if request.config.getoption("--container-runtime") != "docker":
            raise DeploymentException("--homebrew cannot use a container runtime.")
        if request.config.getoption("--cache-backend") not in (None, "redis"):
            raise DeploymentException("--homebrew uses the Redis dependency from the bundled formula.")

        build = request.config.stash.get(_HOMEBREW_BUILD, None)
        if isinstance(build, str):
            raise DeploymentException(build)
        if build is None:
            try:
                with contextlib.ExitStack() as cleanup:
                    build = install_homebrew_build(
                        Path(build_path).resolve(), request.config.getoption("--build-ref"), cleanup,
                    )
                    request.config.add_cleanup(cleanup.pop_all().close)
            except (DeploymentException, OSError) as exc:
                request.config.stash[_HOMEBREW_BUILD] = str(exc)
                raise
            request.config.stash[_HOMEBREW_BUILD] = build
        return HomebrewLRRDeploymentContext(
            build, Path(request.config.getoption("--staging")).resolve(), resource_prefix, port_offset, logger,
        )

    match sys.platform:
        case 'win32':
            windist: str = request.config.getoption("--windist")
            staging_dir: str = request.config.getoption("--staging")
            environment = WindowsLRRDeploymentContext(
                windist, staging_dir, resource_prefix, port_offset,
                logger=logger
            )

        case 'darwin' | 'linux':
            build_path: str = request.config.getoption("--build")
            build_ref: str = request.config.getoption("--build-ref")
            image: str = request.config.getoption("--image")
            git_url: str = request.config.getoption("--git-url")
            git_ref: str = request.config.getoption("--git-ref")
            dockerfile: str = request.config.getoption("--dockerfile")
            use_docker_api: bool = request.config.getoption("--docker-api")
            staging_dir: str = request.config.getoption("--staging")
            cache_backend: str = request.config.getoption("--cache-backend") or "valkey"
            container_runtime = ContainerRuntime(request.config.getoption("--container-runtime"))
            container_url: str = request.config.getoption("--container-url")
            if dockerfile and git_url:
                raise DeploymentException("--dockerfile cannot be combined with --git-url.")
            if dockerfile and image:
                raise DeploymentException("--dockerfile cannot be combined with --image.")

            # Resolve the socket once and export it as DOCKER_HOST for all clients and processes
            socket_url = _resolve_socket(container_runtime, container_url)
            if socket_url:
                os.environ["DOCKER_HOST"] = socket_url
            docker_client = docker.from_env()
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
