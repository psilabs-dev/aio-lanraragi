"""
Python module for setting up and tearing down docker environments for LANraragi.
"""

import contextlib
import io
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import override

import docker
import docker.errors
import docker.models
import docker.models.containers
import docker.models.networks
import docker.models.volumes
from git import Repo

from aio_lanraragi_tests.common import DEFAULT_API_KEY, DEFAULT_REDIS_PORT
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    PluginPathsT,
)
from aio_lanraragi_tests.exceptions import DeploymentException

DEFAULT_REDIS_DOCKER_TAG = "redis:7.2.4"
DEFAULT_LANRARAGI_DOCKER_TAG = "difegue/lanraragi"

LOGGER = logging.getLogger(__name__)

class DockerLRRDeploymentContext(AbstractLRRDeploymentContext):

    """
    Set up a containerized LANraragi environment with Docker.
    This can be used in a pytest function and provided as a fixture.
    """

    @property
    def lrr_image_name(self) -> str:
        return "lanraragi:" + str(self.global_run_id)

    @property
    def redis_container_name(self) -> str:
        return self.resource_prefix + "redis_service"

    @property
    def redis_container(self) -> docker.models.containers.Container | None:
        """
        Returns the redis container from attribute if it exists.
        Otherwise, falls back to finding the redis container with the
        same name, based on initialization settings.
        """
        container = None
        with contextlib.suppress(AttributeError):
            container = self._redis_container
        if container is None:
            container = self._get_container_by_name(self.redis_container_name)
        self._redis_container = container
        return container

    @redis_container.setter
    def redis_container(self, container: docker.models.containers.Container):
        self._redis_container = container

    @property
    def lrr_container_name(self) -> str:
        return self.resource_prefix + "lanraragi_service"

    @property
    def lrr_container(self) -> docker.models.containers.Container | None:
        """
        Returns the LANraragi container from attribute if it exists.
        Otherwise, falls back to finding the LRR container with the
        same name, based on initialization settings.
        """
        container = None
        with contextlib.suppress(AttributeError):
            container = self._lrr_container
        if container is None:
            container = self._get_container_by_name(self.lrr_container_name)
        self._lrr_container = container
        return container

    @lrr_container.setter
    def lrr_container(self, container: docker.models.containers.Container):
        self._lrr_container = container

    @property
    def network_name(self) -> str:
        return self.resource_prefix + "network"

    @property
    def network(self) -> docker.models.networks.Network | None:
        """
        Returns the LANraragi network from attribute if it exists.
        Otherwise, falls back to finding the LRR network with the
        same name, based on initialization settings.
        """
        network = None
        with contextlib.suppress(AttributeError):
            network = self._network
        if network is None:
            network = self._get_network_by_name(self.network_name)
        self._network = network
        return network

    @network.setter
    def network(self, network: docker.models.networks.Network):
        self._network = network

    @override
    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @override
    @property
    def archives_dir(self) -> Path:
        """
        Bind mount for LRR container:/home/koyomi/lanraragi/content.
        """
        dirname = self.resource_prefix + "archives"
        return self.staging_dir / dirname

    @override
    @property
    def logs_dir(self) -> Path:
        """
        Bind mount for LRR container:/home/koyomi/lanraragi/log.
        """
        dirname = self.resource_prefix + "log"
        return self.staging_dir / dirname

    @property
    def thumb_dir(self) -> Path:
        """
        Bind mount for LRR container:/home/koyomi/lanraragi/thumb.
        """
        dirname = self.resource_prefix + "thumb"
        return self.staging_dir / dirname

    @property
    def redis_dir(self) -> Path:
        """
        Bind mount for Redis container:/data.
        """
        dirname = self.resource_prefix + "redis"
        return self.staging_dir / dirname

    @property
    def plugins_root_dir(self) -> Path:
        dirname = self.resource_prefix + "plugins"
        return self.staging_dir / dirname

    @property
    def docker_client(self) -> docker.DockerClient:
        return self._docker_client

    @property
    def docker_api(self) -> docker.APIClient | None:
        """
        Returns API client if docker image build logs streaming is configured, else None.
        """
        return self._docker_api

    @property
    def is_force_build(self) -> bool:
        """
        Force build Docker image, even if the image ID exists.
        """
        return self._is_force_build

    @property
    def dockerfile_path(self) -> Path | None:
        """
        Customizable path to dockerfile. Defaults to `tools/build/docker/Dockerfile`.
        """
        if self._dockerfile_path is not None:
            return self._dockerfile_path
        return Path(self.build_path) / "tools" / "build" / "docker" / "Dockerfile"

    @property
    def redis_container_conf_path(self) -> str:
        """
        Canonical path inside the Redis container where redis.conf is stored.
        """
        return "/home/koyomi/lanraragi/tools/build/docker/redis.conf"

    @property
    def redis_container_data_path(self) -> str:
        """
        Canonical path inside the Redis container where Redis data is stored.
        """
        return "/home/koyomi/lanraragi/database"

    @property
    def lrr_uid(self) -> bool:
        """
        LRR user UID
        """
        if not hasattr(self, "_lrr_uid"):
            self._lrr_uid = os.getuid()
        return self._lrr_uid

    @property
    def lrr_gid(self) -> bool:
        """
        LRR user GID
        """
        if not hasattr(self, "_lrr_gid"):
            self._lrr_gid = os.getgid()
        return self._lrr_gid

    def __init__(
            self, build: str, image: str, git_url: str, git_branch: str, docker_client: docker.DockerClient, staging_dir: str,
            resource_prefix: str, port_offset: int,
            dockerfile: str=None, docker_api: docker.APIClient=None, logger: logging.Logger | None=None,
            global_run_id: int=None, is_allow_uploads: bool=True, is_force_build: bool=False
    ):

        self.resource_prefix = resource_prefix
        self.port_offset = port_offset

        self.build_path: str | None = str(Path(build).resolve()) if build else None
        self.image = image
        self.global_run_id = global_run_id
        self.git_url = git_url
        self.git_branch = git_branch
        self._docker_client = docker_client
        self._docker_api = docker_api
        self._staging_dir = Path(staging_dir)
        if logger is None:
            logger = LOGGER
        self.logger = logger
        self.is_allow_uploads = is_allow_uploads
        self._is_force_build = is_force_build

        if dockerfile is not None:
            dockerfile_path = Path(dockerfile)
            if not dockerfile_path.is_absolute():
                if not build:
                    raise DeploymentException("--dockerfile is a relative path but --build was not provided.")
                dockerfile_path = Path(build) / dockerfile_path
            dockerfile_path = dockerfile_path.resolve()
            if not dockerfile_path.exists():
                raise DeploymentException(f"Dockerfile does not exist: {dockerfile_path}")
            self._dockerfile_path = dockerfile_path
        else:
            self._dockerfile_path = None

    def allow_writable_thumb(self):
        """
        Try to give koyomi the thumb directory.
        """
        resp = self.lrr_container.exec_run(["sh", "-c", 'chown -R koyomi: /home/koyomi/lanraragi/thumb'], demux=True)
        if resp.exit_code != 0:
            raise DeploymentException("Failed to provide koyomi write access to thumbnail directory!")
        return resp

    # by default LRR contents directory is owned by root.
    # to make it writable by the koyomi user, we need to change the ownership.
    def allow_uploads(self):
        self.allow_writable_thumb()
        resp = self.lrr_container.exec_run(["sh", "-c", 'chown -R koyomi: /home/koyomi/lanraragi/content'], demux=True)
        if resp.exit_code != 0:
            raise DeploymentException("Failed to provide koyomi write access to archives directory!")
        return resp

    @override
    def start_lrr(self):
        return self.lrr_container.start()

    @override
    def start_redis(self):
        resp = self.redis_container.start()
        return resp

    @override
    def stop_lrr(self, timeout: int=10):
        """
        Stop the LRR container (timeout in s)
        """
        return self.lrr_container.stop(timeout=timeout)

    @override
    def stop_redis(self, timeout: int=10):
        """
        Stop the redis container (timeout in s)
        """
        self.redis_container.stop(timeout=timeout)

    @override
    def get_lrr_logs(self, tail: int=100) -> bytes:
        """
        Get the LANraragi container logs as bytes.
        """
        if self.lrr_container:
            return self.lrr_container.logs(tail=tail)
        else:
            self.logger.warning("LANraragi container not available for log extraction")
            return b"No LANraragi container available"

    @override
    def get_redis_logs(self, tail: int=100) -> bytes:
        """
        Get the Redis container logs.
        """
        if self.redis_container:
            return self.redis_container.logs(tail=tail)
        else:
            self.logger.warning("Redis container not available for log extraction")
            return b"No Redis container available"

    @override
    def apply_plugins(self):
        plugins_root_dir = self.plugins_root_dir
        if plugins_root_dir.exists():
            shutil.rmtree(plugins_root_dir)
        plugins_root_dir.mkdir(parents=True, exist_ok=False)

        plugin_volumes: dict[str, dict[str, str]] = {}
        for plugin_type, plugin_paths in self.plugin_paths.items():
            if not plugin_paths:
                continue

            source_type_dir = plugins_root_dir / plugin_type
            source_type_dir.mkdir(parents=True, exist_ok=False)

            # Keep test plugins in a dedicated namespace to avoid collisions.
            testing_dir = source_type_dir / "Testing"
            testing_dir.mkdir(parents=True, exist_ok=False)
            for plugin_path in plugin_paths:
                source = Path(plugin_path)
                if not source.exists():
                    raise FileNotFoundError(f"Plugin path does not exist: {source}")
                shutil.copy2(source, testing_dir / source.name)

            plugin_volumes[str(source_type_dir)] = {
                "bind": f"/home/koyomi/lanraragi/lib/LANraragi/Plugin/{plugin_type}",
                "mode": "ro",
            }

        return plugin_volumes

    @override
    def setup(
        self, with_api_key: bool=False, with_nofunmode: bool=False, enable_cors: bool=False, lrr_debug_mode: bool=False,
        environment: dict[str, str]={}, plugin_paths: PluginPathsT={},
        test_connection_max_retries: int=4
    ):
        """
        Main entrypoint to setting up a LRR docker environment. Pulls/builds required images,
        creates/recreates required volumes, containers, networks, and connects them together,
        as well as any other configuration.

        Args:
            with_api_key: whether to add a default API key to LRR
            with_nofunmode: whether to start LRR with nofunmode on
            enable_cors: whether to enable/disable CORS during startup
            lrr_debug_mode: whether to start LRR with debug mode on
            environment: additional environment variables map to pass through to LRR during container creation
            test_connection_max_retries: Number of attempts to connect to the LRR server. Usually resolves after 2, unless there are many files.
        """
        # ensure staging, contents, thumb, and Redis directories exist
        staging_dir = self.staging_dir
        if not staging_dir:
            raise FileNotFoundError("Staging directory not provided. Use --staging to specify a host directory for bind mounts.")
        if not staging_dir.exists():
            raise FileNotFoundError(f"Staging directory {staging_dir} not found.")
        self.plugin_paths = plugin_paths
        plugin_volumes: dict[str, dict[str, str]] = self.apply_plugins()

        contents_dir = self.archives_dir
        thumb_dir = self.thumb_dir
        logs_dir = self.logs_dir
        redis_dir = self.redis_dir
        if contents_dir.exists():
            self.logger.debug(f"Contents directory exists: {contents_dir}")
        else:
            self.logger.debug(f"Creating contents directory: {contents_dir}")
            contents_dir.mkdir(parents=True, exist_ok=False)
        if thumb_dir.exists():
            self.logger.debug(f"Thumb directory exists: {thumb_dir}")
        else:
            self.logger.debug(f"Creating thumb directory: {thumb_dir}")
            thumb_dir.mkdir(parents=True, exist_ok=False)
        if logs_dir.exists():
            self.logger.debug(f"Logs directory exists: {logs_dir}")
        else:
            self.logger.debug(f"Creating logs dir: {logs_dir}")
            logs_dir.mkdir(parents=True, exist_ok=False)
        if redis_dir.exists():
            self.logger.debug(f"Redis directory exists: {redis_dir}")
        else:
            self.logger.debug(f"Creating Redis dir: {redis_dir}")
            redis_dir.mkdir(parents=True, exist_ok=False)

            # Brief delay for VirtioFS to propagate the
            # newly created directory before it is used as a bind mount source.
            if sys.platform == "darwin":
                time.sleep(1)

        # log the setup resource allocations for user to see
        # the docker image is not included, haven't decided how to classify it yet.
        self.logger.info(
            f"Deploying Docker LRR with the following resources: "
            f"LRR container {self.lrr_container_name}, Redis container {self.redis_container_name}, "
            f"contents path {contents_dir}, thumb path {thumb_dir}, logs path {logs_dir}, redis path {redis_dir}, "
            f"network {self.network_name}"
        )

        # >>>>> IMAGE PREPARATION >>>>>
        image_id = self.lrr_image_name
        redis_conf_src: Path | None = None
        if build_path := self.build_path:
            self.logger.info(f"Building LRR image {image_id} from build path {build_path}.")
            self._build_docker_image(build_path, force=self.is_force_build)
            redis_conf_src = Path(build_path) / "tools" / "build" / "docker" / "redis.conf"
        elif self.git_url:
            # When building by git URL, we always clone the repository and rebuild.
            self.logger.info(f"Building LRR image {image_id} from git URL {self.git_url}.")
            with tempfile.TemporaryDirectory() as tmpdir:
                self.logger.debug(f"Cloning {self.git_url} to {tmpdir}...")
                repo_dir = Path(tmpdir) / "LANraragi"
                repo = Repo.clone_from(self.git_url, repo_dir)
                if self.git_branch: # throws git.exc.GitCommandError if branch does not exist.
                    repo.git.checkout(self.git_branch)
                self._build_docker_image(repo.working_dir, force=True)
                conf_in_repo = Path(repo.working_dir) / "tools" / "build" / "docker" / "redis.conf"
                if conf_in_repo.exists():
                    redis_conf_src = self.staging_dir / (self.resource_prefix + "redis.conf")
                    shutil.copy2(conf_in_repo, redis_conf_src)
                else:
                    self.logger.warning(f"Redis conf not found in cloned repo: {conf_in_repo}")
        else:
            image = DEFAULT_LANRARAGI_DOCKER_TAG
            if self.image:
                image = self.image
            self.logger.info(f"Pulling LRR image from Docker Hub: {image}.")
            self._pull_docker_image_if_not_exists(image, force=False)
            self.docker_client.images.get(image).tag(image_id)
            redis_conf_src = self._extract_redis_conf_from_image(image_id)

        # TODO: why does this exist
        self._applied_redis_conf_src = redis_conf_src

        # pull redis
        self._pull_docker_image_if_not_exists(DEFAULT_REDIS_DOCKER_TAG, force=False)
        # <<<<< IMAGE PREPARATION <<<<<

        # prepare the network
        network_name = self.network_name
        if not self.network:
            self.logger.debug(f"Creating network: {network_name}.")
            self.network = self.docker_client.networks.create(network_name, driver="bridge")
        else:
            self.logger.debug(f"Network exists: {network_name}.")

        # prepare the redis container first.
        redis_port = self.redis_port
        redis_container_name = self.redis_container_name
        redis_healthcheck = {
            "test": [ "CMD", "redis-cli", "--raw", "incr", "ping" ],
            "start_period": 1000000 * 1000 # 1s
        }
        redis_ports = {
            "6379/tcp": redis_port
        }
        redis_command = (
            [
                "redis-server", self.redis_container_conf_path,
                "--bind", "0.0.0.0",
                "--logfile", "",
                "--pidfile", "/home/koyomi/lanraragi/database/redis.pid",
                "--protected-mode", "no",
            ]
            if redis_conf_src
            else None
        )
        redis_volumes = {str(redis_dir): {"bind": self.redis_container_data_path, "mode": "rw"}}
        if redis_conf_src:
            redis_volumes[str(redis_conf_src)] = {
                "bind": self.redis_container_conf_path,
                "mode": "ro",
            }
        if self.redis_container:
            self.logger.debug(f"Redis container exists: {self.redis_container_name}.")
            # if such a container exists, assume it is already configured with the correct volumes and networks
            # which we have already done so in previous steps. We may also skip the "need-restart" checks,
            # since redis is not the image we're testing here and the volumes are what carry data.
        else:
            self.logger.debug(f"Creating redis container: {self.redis_container_name}")
            self.redis_container = self.docker_client.containers.create(
                DEFAULT_REDIS_DOCKER_TAG,
                name=redis_container_name,
                command=redis_command,
                user=f"{self.lrr_uid}:{self.lrr_gid}",  # unix UID:GID
                hostname=redis_container_name,
                detach=True,
                network=network_name,
                ports=redis_ports,
                healthcheck=redis_healthcheck,
                volumes=redis_volumes,
            )

        # then prepare the LRR container.
        lrr_port = self.lrr_port
        lrr_container_name = self.lrr_container_name
        lrr_ports = {
            "3000/tcp": lrr_port
        }
        lrr_environment = [
            f"LRR_REDIS_ADDRESS={redis_container_name}:{DEFAULT_REDIS_PORT}",
            f"LRR_UID={self.lrr_uid}",  # unix UID
            f"LRR_GID={self.lrr_gid}"   # unix GID
        ]
        # Apply user-provided environment, overriding defaults when keys overlap
        desired_env_map = {
            "LRR_REDIS_ADDRESS": f"{redis_container_name}:{DEFAULT_REDIS_PORT}",
            "LRR_UID": str(self.lrr_uid),
            "LRR_GID": str(self.lrr_gid),
        }
        if environment:
            desired_env_map.update(environment)
        desired_env_list = [f"{k}={v}" for k, v in desired_env_map.items()]
        lrr_environment = desired_env_list
        create_lrr_container = False
        if self.lrr_container:
            self.logger.debug(f"LRR container exists: {self.lrr_container_name}.")
            # in this situation, whether we restart the LRR container depends on whether or not the images used for both containers
            # match.
            needs_recreate_lrr = self.lrr_container.image.id != self.docker_client.images.get(image_id).id
            # If environment differs from desired, recreate to apply env
            self.lrr_container.reload()
            current_env_list: list[str] = self.lrr_container.attrs["Config"]["Env"]
            if not needs_recreate_lrr and set(current_env_list) != set(lrr_environment):
                self.logger.debug("LRR environment differs from desired; removing existing container for recreation.")
                needs_recreate_lrr = True
            if not needs_recreate_lrr and plugin_volumes:
                self.logger.debug("Test-time plugin mounts are configured; removing existing container for recreation.")
                needs_recreate_lrr = True
            if needs_recreate_lrr:
                self.logger.debug("LRR Image hash has been updated: removing existing container.")
                self.lrr_container.stop(timeout=1)
                self.lrr_container.remove(force=True)
                create_lrr_container = True
            else:
                self.logger.debug("LRR image hash is same; container will not be recreated.")
        else:
            create_lrr_container = True
        if create_lrr_container:
            self.logger.debug(f"Creating LRR container: {self.lrr_container_name}")
            lrr_volumes = {
                str(contents_dir): {"bind": "/home/koyomi/lanraragi/content", "mode": "rw"},
                str(thumb_dir): {"bind": "/home/koyomi/lanraragi/thumb", "mode": "rw"},
                str(logs_dir): {"bind": "/home/koyomi/lanraragi/log", "mode": "rw"},
            }
            lrr_volumes.update(plugin_volumes)
            self.lrr_container = self.docker_client.containers.create(
                image_id, hostname=lrr_container_name, name=lrr_container_name, detach=True, network=network_name, ports=lrr_ports, environment=lrr_environment,
                volumes=lrr_volumes
            )
            self.logger.debug("LRR container created.")

        # start redis
        self.logger.debug(f"Starting container: {self.redis_container_name}")
        self.start_redis()
        self.test_redis_connection()
        self.logger.debug("Redis container started.")
        self.logger.debug("Running Redis post-startup configuration.")
        if with_api_key:
            self.update_api_key(DEFAULT_API_KEY)
        if with_nofunmode:
            self.enable_nofun_mode()
        if lrr_debug_mode:
            self.enable_lrr_debug_mode()
        if enable_cors:
            self.enable_cors()
        else:
            self.disable_cors()
        self.logger.debug("Redis post-connect configuration complete.")

        # start lrr
        self.start_lrr()
        self.logger.debug("Testing connection to LRR server.")
        self.test_lrr_connection(self.lrr_port, test_connection_max_retries)
        if self.is_allow_uploads:
            resp = self.allow_uploads()
            if resp.exit_code != 0:
                raise DeploymentException(f"Failed to modify permissions for LRR contents: {resp}")
        self.logger.debug("LRR server is ready.")

    @override
    def start(self, test_connection_max_retries: int=4):
        # this can't really be replaced with setup stage, because during setup we do some work after redis startup
        # and before LRR startup.
        self.logger.debug(f"Starting container: {self.redis_container_name}")
        self.redis_container.start()
        self.logger.debug("Redis container started.")

        self.start_lrr()
        self.logger.debug("Testing connection to LRR server.")
        self.test_lrr_connection(self.lrr_port, test_connection_max_retries)
        if self.is_allow_uploads:
            resp = self.allow_uploads()
            if resp.exit_code != 0:
                raise DeploymentException(f"Failed to modify permissions for LRR contents: {resp}")
        self.logger.debug("LRR server is ready.")

    @override
    def stop(self):
        """
        Stops the LRR and Redis docker containers.

        WARNING: stopping container does NOT necessarily make the corresponding ports available.
        It is possible that the docker daemon still reserves the port, and may not free it until
        the underlying network configurations are updated, which can take up to a minute. See:

        - https://docs.docker.com/engine/network/packet-filtering-firewalls
        - https://stackoverflow.com/questions/63467759/close-docker-port-when-container-is-stopped

        All this is to say, do not use port availability as an indicator that a container is
        successfully stopped.
        """
        if self.lrr_container:
            self.lrr_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.lrr_container_name}")
        if self.redis_container:
            self.redis_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.redis_container_name}")

    @override
    def restart(self):
        """
        Basically stop and start, except we don't do the check on port availability.
        """
        if self.lrr_container:
            self.lrr_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.lrr_container_name}")
        if self.redis_container:
            self.redis_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.redis_container_name}")
        self.logger.debug(f"Starting container: {self.redis_container_name}")
        self.redis_container.start()
        self.logger.debug("Redis container started.")
        self.start_lrr()
        self.logger.debug("Testing connection to LRR server.")
        self.test_lrr_connection(self.lrr_port)
        if self.is_allow_uploads:
            resp = self.allow_uploads()
            if resp.exit_code != 0:
                raise DeploymentException(f"Failed to modify permissions for LRR contents: {resp}")
        self.logger.debug("LRR server is ready.")

    @override
    def teardown(self, remove_data: bool=False):
        """
        Remove all resources and close all closable resources/clients/connections.
        """
        self._reset_docker_test_env(remove_data=remove_data)
        if hasattr(self, "_redis_client") and self._redis_client is not None:
            self._redis_client.close()
        if self._docker_api:
            self._docker_api.close()
        if self._docker_client:
            self._docker_client.close()
        self.logger.info("Cleanup complete.")

    def _get_container_by_name(self, container_name: str) -> docker.models.containers.Container | None:
        """
        Tries to return a container DTO by its name if exists. Otherwise, returnes None.
        """
        with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
            container = self.docker_client.containers.get(container_name)
            return container
        return None

    def _get_volume_by_name(self, volume_name: str) -> docker.models.volumes.Volume | None:
        """
        Tries to return a volume DTO by its name if exists. Otherwise, returnes None.
        """
        with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
            container = self.docker_client.volumes.get(volume_name)
            return container
        return None

    def _get_network_by_name(self, network_name: str) -> docker.models.networks.Network | None:
        """
        Tries to return a network DTO by its name if exists. Otherwise, returnes None.
        """
        with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
            container = self.docker_client.networks.get(network_name)
            return container
        return None

    def _exec_redis_cli(self, command: str) -> docker.models.containers.ExecResult:
        """
        Executes a command on the redis container.
        """
        container = self.redis_container
        if container is None:
            raise DeploymentException("No redis container found!")
        return container.exec_run(["bash", "-c", f'redis-cli <<EOF\n{command}\nEOF'])

    def _extract_redis_conf_from_image(self, image_id: str) -> Path:
        """
        Create an ephemeral container from the LRR image and extract
        tools/build/docker/redis.conf into the staging directory.

        Throws deployment exception if redis.conf is not found.
        """
        container = self.docker_client.containers.create(image_id)
        try:
            # get the canonical path to redis.conf from the docker image.
            tar_stream, _ = container.get_archive(self.redis_container_conf_path)
            dest = self.staging_dir / (self.resource_prefix + "redis.conf")
            with tarfile.open(fileobj=io.BytesIO(b"".join(tar_stream))) as tf:
                member = tf.getmember("redis.conf")
                dest.write_bytes(tf.extractfile(member).read())
            return dest
        except docker.errors.NotFound:
            # self.logger.warning("redis.conf not found in LRR image.")
            # return None
            raise DeploymentException(f"redis.conf not found in docker image: {image_id}")
        finally:
            container.remove()

    def _reset_docker_test_env(self, remove_data: bool=False):
        """
        Reset docker test environment (LRR and Redis containers, testing network) between tests.
        Stops containers, then removes them. Then, removes the data (if applied). Finally removes
        the network.

        To handle potential FS stale cache issues, a forceful removal will be attempted within containers,
        before they are shut down and an external bind mount source removal is invoked.

        If something goes wrong during setup, the environment will be reset and the data should be removed.
        """
        if remove_data and self.lrr_container:
            self.lrr_container.reload()
            status = self.lrr_container.status
            if status == 'running':
                self.lrr_container.exec_run(["sh", "-c", "s6-svc -d /run/service/lanraragi"])
                self.lrr_container.exec_run(["sh", "-c", "s6-svc -d /run/service/redis"])
                time.sleep(1)
                self.lrr_container.exec_run(["sh", "-c", 'rm -rf /home/koyomi/lanraragi/content/*'], user='root')
                self.lrr_container.exec_run(["sh", "-c", 'rm -rf /home/koyomi/lanraragi/thumb/*'], user='root')
                self.lrr_container.exec_run(["sh", "-c", 'rm -rf /home/koyomi/lanraragi/log/*'], user='root')
            else:
                self.logger.info(f"Container not running with status {status} (no teardown commands run): {self.lrr_container_name}")
        if self.lrr_container:
            self.lrr_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.lrr_container_name}")
        if self.lrr_container:
            self.lrr_container.remove(v=True, force=True)
            self.logger.debug(f"Removed container: {self.lrr_container_name}")

        if remove_data and self.redis_container and self.redis_container.status == 'running':
            self.redis_container.exec_run(["bash", "-c", f"rm -rf {self.redis_container_data_path}/*"], user='redis')
        if self.redis_container:
            self.redis_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.redis_container_name}")
        if self.redis_container:
            self.redis_container.remove(v=True, force=True)
            self.logger.debug(f"Removed container: {self.redis_container_name}")

        if remove_data:
            if self.redis_dir.exists():
                shutil.rmtree(self.redis_dir)
                self.logger.debug(f"Removed redis directory: {self.redis_dir}")
            if self.archives_dir.exists():
                shutil.rmtree(self.archives_dir)
                self.logger.debug(f"Removed contents directory: {self.archives_dir}")
            if self.thumb_dir.exists():
                shutil.rmtree(self.thumb_dir)
                self.logger.debug(f"Removed thumb directory: {self.thumb_dir}")
            if self.logs_dir.exists():
                shutil.rmtree(self.logs_dir)
                self.logger.debug(f"Removed logs directory: {self.logs_dir}")
            if self.plugins_root_dir.exists():
                shutil.rmtree(self.plugins_root_dir)
                self.logger.debug(f"Removed plugins directory: {self.plugins_root_dir}")
            redis_conf_staging = self.staging_dir / (self.resource_prefix + "redis.conf")
            if redis_conf_staging.exists():
                redis_conf_staging.unlink()
                self.logger.debug(f"Removed staged redis conf: {redis_conf_staging}")

        if hasattr(self, 'network') and self.network:
            self.network.remove()
            self.logger.debug(f"Removed network: {self.network_name}")

    def _build_docker_image(self, build_path: str, force: bool=False):
        """
        Build a docker image.

        Args:
            build_path: The path to the build directory.
            force: Whether to force the build (e.g. even if the image already exists).

        Raises:
            FileNotFoundError: docker image or build path not found
            DeploymentException: if docker image build fails with log stream output
            docker.errors.BuildError: if docker image build fails with log stream disabled
        """
        image_id = self.lrr_image_name

        if not force:
            try:
                self.docker_client.images.get(image_id)
                self.logger.debug(f"Image {image_id} already exists, skipping build.")
                return
            except docker.errors.ImageNotFound:
                self.logger.debug(f"Image {image_id} not found, building.")
                self._build_docker_image(build_path, force=True)
                return

        if not Path(build_path).exists():
            raise FileNotFoundError(f"Build path {build_path} does not exist!")

        dockerfile_path = self.dockerfile_path
        if not dockerfile_path.exists():
            raise FileNotFoundError(f"Dockerfile {dockerfile_path} does not exist!")

        self.logger.debug(f"Building LRR image; this can take a while ({dockerfile_path}).")
        build_start = time.time()

        # https://docker-py.readthedocs.io/en/stable/api.html
        # force-remove intermediate artifacts with rm/forcerm.
        if self.docker_api:
            logs = []
            for evt in self.docker_api.build(path=build_path, dockerfile=dockerfile_path, tag=image_id, decode=True, rm=True, forcerm=True):
                logs.append(evt)
                if (msg := evt.get("stream")):
                    self.logger.info(msg.strip())
                if "error" in evt or "errorDetail" in evt:
                    error_msg = evt.get("error") or evt.get("errorDetail", {}).get("message")
                    raise DeploymentException(f"Docker image build failed! Error: {error_msg}")
        else:
            self.docker_client.images.build(path=build_path, dockerfile=dockerfile_path, tag=image_id, rm=True, forcerm=True)

        build_time = time.time() - build_start
        self.logger.info(f"LRR image {image_id} build complete: time {build_time}s")
        return

    def _pull_docker_image_if_not_exists(self, image: str, force: bool=False):
        """
        Pull a docker image if it does not exist.

        Args:
            image: The name of the image to pull.
            force: Whether to force the pull (e.g. even if the image already exists).
        """

        if force:
            self.docker_client.images.pull(image)
            return
        else:
            self.logger.debug(f"Checking if {image} exists.")
            try:
                self.docker_client.images.get(image)
                self.logger.debug(f"{image} already exists, skipping pull.")
                return
            except docker.errors.ImageNotFound:
                self.logger.debug(f"{image} not found, pulling.")
                self.docker_client.images.pull(image)
                return
