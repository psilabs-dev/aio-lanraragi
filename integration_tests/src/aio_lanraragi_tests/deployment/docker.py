"""
Python module for setting up and tearing down docker environments for LANraragi.
"""

import contextlib
import json
import logging
from pathlib import Path
import tempfile
import time
from typing import Optional, override
import docker
import docker.errors
import docker.models
import docker.models.containers
import docker.models.networks
from git import Repo

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.common import DEFAULT_API_KEY, DEFAULT_LRR_PORT, DEFAULT_REDIS_PORT

DEFAULT_REDIS_DOCKER_TAG = "redis:7.2.4"
DEFAULT_LANRARAGI_DOCKER_TAG = "difegue/lanraragi"

LOGGER = logging.getLogger(__name__)

class DockerLRRDeploymentContext(AbstractLRRDeploymentContext):

    """
    Set up a containerized LANraragi environment with Docker.
    This can be used in a pytest function and provided as a fixture.
    """
    
    def __init__(
            self, build: str, image: str, git_url: str, git_branch: str, docker_client: docker.DockerClient,
            docker_api: docker.APIClient=None, logger: Optional[logging.Logger]=None,
            global_run_id: int=None, is_allow_uploads: bool=True,
    ):

        self.build_path = build
        self.image = image
        self.global_run_id = global_run_id
        self.git_url = git_url
        self.git_branch = git_branch
        self.docker_client = docker_client
        self.docker_api = docker_api
        self.redis_container: docker.models.containers.Container = None
        self.lrr_container: docker.models.containers.Container = None
        if logger is None:
            logger = LOGGER
        self.logger = logger
        self.is_allow_uploads = is_allow_uploads

    @override
    def get_logger(self) -> logging.Logger:
        return self.logger

    @override
    def update_api_key(self, api_key: Optional[str]):
        if api_key is None:
            return self.redis_container.exec_run(["bash", "-c", 'redis-cli <<EOF\nSELECT 2\nHDEL LRR_CONFIG apikey\nEOF'])
        else:
            return self.redis_container.exec_run(["bash", "-c", f'redis-cli <<EOF\nSELECT 2\nHSET LRR_CONFIG apikey {api_key}\nEOF'])

    @override
    def enable_nofun_mode(self):
        return self.redis_container.exec_run(["bash", "-c", 'redis-cli <<EOF\nSELECT 2\nHSET LRR_CONFIG nofunmode 1\nEOF'])

    @override
    def disable_nofun_mode(self):
        return self.redis_container.exec_run(["bash", "-c", 'redis-cli <<EOF\nSELECT 2\nHSET LRR_CONFIG nofunmode 0\nEOF'])

    # by default LRR contents directory is owned by root.
    # to make it writable by the koyomi user, we need to change the ownership.
    @override
    def allow_uploads(self):
        return self.lrr_container.exec_run(["sh", "-c", 'chown -R koyomi: content'])

    @override
    def restart(self):
        self.stop_lrr()
        self.start_lrr()
        self.get_logger().debug("Testing connection to LRR server.")
        self.test_lrr_connection(self.get_lrr_port())

    @override
    def start_lrr(self):
        return self.lrr_container.start()
    
    @override
    def start_redis(self):
        return self.redis_container.start()

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
        return self.redis_container.stop(timeout=timeout)

    @override
    def get_lrr_logs(self, tail: int=100) -> bytes:
        """
        Get the LANraragi container logs as bytes.
        """
        if self.lrr_container:
            return self.lrr_container.logs(tail=tail)
        else:
            self.get_logger().warning("LANraragi container not available for log extraction")
            return b"No LANraragi container available"

    def get_redis_logs(self, tail: int=100) -> bytes:
        """
        Get the Redis container logs.
        """
        if self.redis_container:
            return self.redis_container.logs(tail=tail)
        else:
            self.get_logger().warning("Redis container not available for log extraction")
            return b"No Redis container available"

    @override
    def setup(
        self, resource_prefix: str, port_offset: int,
        with_api_key: bool=False, with_nofunmode: bool=False, 
        test_connection_max_retries: int=4
    ):
        """
        Main entrypoint to setting up a LRR docker environment. Pulls/builds required images,
        creates/recreates required volumes, containers, networks, and connects them together,
        as well as any other configuration.

        Args:
            resource_prefix: prefix to use for resource names
            port_offset: offset to use for port numbers
            test_connection_max_retries: Number of attempts to connect to the LRR server. Usually resolves after 2, unless there are many files.
        """

        self.resource_prefix = resource_prefix
        self.port_offset = port_offset

        # prepare images
        if self.build_path:
            self.get_logger().info(f"Building LRR image {self._get_image_name_from_global_run_id()} from build path {self.build_path}.")
            self._build_docker_image(self.build_path, force=False)
        elif self.git_url:
            self.get_logger().info(f"Building LRR image {self._get_image_name_from_global_run_id()} from git URL {self.git_url}.")
            try:
                self.docker_client.images.get(self._get_image_name_from_global_run_id())
                self.get_logger().info(f"Image {self._get_image_name_from_global_run_id()} already exists, skipping build.")
            except docker.errors.ImageNotFound:
                with tempfile.TemporaryDirectory() as tmpdir:
                    self.get_logger().info(f"Cloning {self.git_url} to {tmpdir}...")
                    repo_dir = Path(tmpdir) / "LANraragi"
                    repo = Repo.clone_from(self.git_url, repo_dir)
                    if self.git_branch: # throws git.exc.GitCommandError if branch does not exist.
                        repo.git.checkout(self.git_branch)
                    self._build_docker_image(repo.working_dir, force=True)
        else:
            self.get_logger().info(f"Pulling LRR image from Docker Hub {self.image}.")
            image = DEFAULT_LANRARAGI_DOCKER_TAG
            if self.image:
                image = self.image
            self._pull_docker_image_if_not_exists(image, force=False)
            self.docker_client.images.get(image).tag(self._get_image_name_from_global_run_id())

        # check testing environment availability
        # raise a testing exception if these conditions are violated.
        container: docker.models.containers.Container
        for container in self.docker_client.containers.list(all=True):
            if container.name in {self._get_lrr_container_name(), self._get_redis_container_name()}:
                raise DeploymentException(f"Container {container.name} exists!")
        network: docker.models.networks.Network
        for network in self.docker_client.networks.list():
            if network.name == self._get_network_name():
                raise DeploymentException(f"Network {network.name} exists!")

        # pull redis
        self._pull_docker_image_if_not_exists(DEFAULT_REDIS_DOCKER_TAG, force=False)

        self.get_logger().info("Creating test network.")
        network = self.docker_client.networks.create(self._get_network_name(), driver="bridge")
        self.network = network

        # create containers
        self.get_logger().info("Creating containers.")
        # ensure volumes exist
        self.get_logger().info("Ensuring test volumes.")
        self._ensure_volume(self._get_lrr_contents_volume_name())
        self._ensure_volume(self._get_lrr_thumb_volume_name())
        self._ensure_volume(self._get_redis_volume_name())
        redis_healthcheck = {
            "test": [ "CMD", "redis-cli", "--raw", "incr", "ping" ],
            "start_period": 1000000 * 1000 # 1s
        }
        redis_ports = {
            "6379/tcp": self._get_redis_port()
        }
        self.redis_container = self.docker_client.containers.create(
            DEFAULT_REDIS_DOCKER_TAG,
            name=self._get_redis_container_name(),
            hostname=self._get_redis_container_name(),
            detach=True,
            network=self._get_network_name(),
            ports=redis_ports,
            healthcheck=redis_healthcheck,
            volumes={
                self._get_redis_volume_name(): {"bind": "/data", "mode": "rw"}
            }
        )

        lrr_ports = {
            "3000/tcp": self.get_lrr_port()
        }
        lrr_environment = [
            f"LRR_REDIS_ADDRESS={self._get_redis_container_name()}:{DEFAULT_REDIS_PORT}"
        ]
        self.lrr_container = self.docker_client.containers.create(
            self._get_image_name_from_global_run_id(), hostname=self._get_lrr_container_name(), name=self._get_lrr_container_name(), detach=True, network=self._get_network_name(), ports=lrr_ports, environment=lrr_environment,
            volumes={
                self._get_lrr_contents_volume_name(): {"bind": "/home/koyomi/lanraragi/content", "mode": "rw"},
                self._get_lrr_thumb_volume_name(): {"bind": "/home/koyomi/lanraragi/thumb", "mode": "rw"}
            }
        )

        # start database
        self.get_logger().info("Starting database.")
        self.start_redis()

        self.get_logger().debug("Running post-startup configuration.")
        if with_api_key:
            resp = self.update_api_key(DEFAULT_API_KEY)
            if resp.exit_code != 0:
                self._reset_docker_test_env(remove_data=True)
                raise DeploymentException(f"Failed to add API key to server: {resp}")

        if with_nofunmode:
            resp = self.enable_nofun_mode()
            if resp.exit_code != 0:
                self._reset_docker_test_env(remove_data=True)
                raise DeploymentException(f"Failed to enable nofunmode: {resp}")

        # start lrr
        self.start_lrr()

        # post LRR startup
        self.get_logger().debug("Testing connection to LRR server.")
        self.test_lrr_connection(self.get_lrr_port(), test_connection_max_retries)

        # allow uploads
        if self.is_allow_uploads:
            resp = self.allow_uploads()
            if resp.exit_code != 0:
                self._reset_docker_test_env(remove_data=True)
                raise DeploymentException(f"Failed to modify permissions for LRR contents: {resp}")

        self.get_logger().info("Environment setup complete, proceeding to testing...")

    @override
    def teardown(self, remove_data: bool=False):
        self._reset_docker_test_env(remove_data=remove_data)
        # with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
        #     self.docker_client.images.get(self._get_image_name_from_global_run_id()).remove(force=True)
        self.get_logger().info("Cleanup complete.")

    def _get_lrr_contents_volume_name(self) -> str:
        return f"{self.resource_prefix}lanraragi_contents"

    def _get_lrr_thumb_volume_name(self) -> str:
        return f"{self.resource_prefix}lanraragi_thumb"

    def _get_redis_volume_name(self) -> str:
        return f"{self.resource_prefix}redis_data"

    def _get_network_name(self) -> str:
        return f"{self.resource_prefix}network"

    def _get_lrr_container_name(self) -> str:
        return f"{self.resource_prefix}lanraragi_service"

    def _get_redis_container_name(self) -> str:
        return f"{self.resource_prefix}redis_service"

    def _get_lrr_image_name(self) -> str:
        return f"integration_test_lanraragi:{self.global_run_id}"

    @override
    def get_lrr_port(self) -> int:
        return DEFAULT_LRR_PORT + self.port_offset

    def _get_redis_port(self) -> int:
        return DEFAULT_REDIS_PORT + self.port_offset

    def _reset_docker_test_env(self, remove_data: bool=False):
        """
        Reset docker test environment (LRR and Redis containers, testing network) between tests.
        
        If something goes wrong during setup, the environment will be reset and the data should be removed.
        """
        if self.lrr_container:
            with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
                container = self.docker_client.containers.get(self.lrr_container.id)
                container.stop(timeout=1)
                container.remove(force=True)
        if self.redis_container:
            with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
                container = self.docker_client.containers.get(self.redis_container.id)
                container.stop(timeout=1)
                container.remove(force=True)
        if hasattr(self, 'network') and self.network:
            with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
                self.docker_client.networks.get(self.network.id).remove()
        
        if remove_data:
            self._remove_volume_if_exists(self._get_lrr_contents_volume_name())
            self._remove_volume_if_exists(self._get_lrr_thumb_volume_name())
            self._remove_volume_if_exists(self._get_redis_volume_name())

    def _ensure_volume(self, name: str):
        """
        Ensure a named Docker volume exists; create it if missing.
        """
        self.get_logger().debug(f"Checking if volume {name} exists.")
        try:
            self.docker_client.volumes.get(name)
            self.get_logger().debug(f"Volume {name} already exists, skipping creation.")
        except docker.errors.NotFound:
            self.get_logger().debug(f"Creating volume {name}.")
            self.docker_client.volumes.create(name=name)

    def _remove_volume_if_exists(self, name: str):
        """
        Remove a named Docker volume if it exists.
        """
        with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
            self.get_logger().debug(f"Removing volume {name}.")
            self.docker_client.volumes.get(name).remove(force=True)

    def _build_docker_image(self, build_path: Path, force: bool=False):
        """
        Build a docker image.

        Args:
            build_path: The path to the build directory.
            force: Whether to force the build (e.g. even if the image already exists).
        """

        if force:
            if not Path(build_path).exists():
                raise FileNotFoundError(f"Build path {build_path} does not exist!")
            dockerfile_path = Path(build_path) / "tools" / "build" / "docker" / "Dockerfile"
            if not dockerfile_path.exists():
                raise FileNotFoundError(f"Dockerfile {dockerfile_path} does not exist!")
            self.get_logger().info(f"Building LRR image; this can take a while ({dockerfile_path}).")
            build_start = time.time()
            if self.docker_api:
                for lineb in self.docker_api.build(path=build_path, dockerfile=dockerfile_path, tag=self._get_image_name_from_global_run_id()):
                    if (data := json.loads(lineb.decode('utf-8').strip())) and (stream := data.get('stream')):
                        self.get_logger().info(stream.strip())
            else:
                self.docker_client.images.build(path=build_path, dockerfile=dockerfile_path, tag=self._get_image_name_from_global_run_id())
            build_time = time.time() - build_start
            self.get_logger().info(f"LRR image {self._get_image_name_from_global_run_id()} build complete: time {build_time}s")
            return
        else:
            try:
                self.docker_client.images.get(self._get_image_name_from_global_run_id())
                self.get_logger().info(f"Image {self._get_image_name_from_global_run_id()} already exists, skipping build.")
                return
            except docker.errors.ImageNotFound:
                self.get_logger().info(f"Image {self._get_image_name_from_global_run_id()} not found, building.")
                self._build_docker_image(build_path, force=True)
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
            self.get_logger().debug(f"Checking if {image} exists.")
            try:
                self.docker_client.images.get(image)
                self.get_logger().debug(f"{image} already exists, skipping pull.")
                return
            except docker.errors.ImageNotFound:
                self.get_logger().debug(f"{image} not found, pulling.")
                self.docker_client.images.pull(image)
                return

    def _get_image_name_from_global_run_id(self) -> str:
        """
        Get the unique name for the image for this global pytest run.

        Allows for docker deployment context to build once and reuse for multiple tests.
        """
        return f"lanraragi:{self.global_run_id}"
