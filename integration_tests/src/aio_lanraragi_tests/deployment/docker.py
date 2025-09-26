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
import docker.models.volumes
from git import Repo

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.exceptions import DeploymentException
from aio_lanraragi_tests.common import DEFAULT_API_KEY, DEFAULT_REDIS_PORT

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
    def redis_container(self) -> Optional[docker.models.containers.Container]:
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
    def lrr_container(self) -> Optional[docker.models.containers.Container]:
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
    def network(self) -> Optional[docker.models.networks.Network]:
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
    
    @property
    def lrr_contents_volume_name(self) -> str:
        return self.resource_prefix + "lanraragi_contents"

    @property
    def lrr_contents_volume(self) -> Optional[docker.models.volumes.Volume]:
        """
        Returns the LANraragi contents volume from attribute if it exists.
        Otherwise, falls back to finding the LRR volume with the
        same name, based on initialization settings.
        """
        volume = None
        with contextlib.suppress(AttributeError):
            volume = self._lrr_contents_volume
        if volume is None:
            volume = self._get_volume_by_name(self.lrr_contents_volume_name)
        self._lrr_contents_volume = volume
        return volume

    @lrr_contents_volume.setter
    def lrr_contents_volume(self, volume: docker.models.volumes.Volume):
        self._lrr_contents_volume = volume

    @property
    def lrr_thumb_volume_name(self) -> str:
        return self.resource_prefix + "lanraragi_thumb"

    @property
    def lrr_thumb_volume(self) -> Optional[docker.models.volumes.Volume]:
        """
        Returns the LANraragi thumb volume from attribute if it exists.
        Otherwise, falls back to finding the LRR volume with the
        same name, based on initialization settings.
        """
        volume = None
        with contextlib.suppress(AttributeError):
            volume = self._lrr_thumb_volume
        if volume is None:
            volume = self._get_volume_by_name(self.lrr_thumb_volume_name)
        self._lrr_thumb_volume = volume
        return volume

    @lrr_thumb_volume.setter
    def lrr_thumb_volume(self, volume: docker.models.volumes.Volume):
        self._lrr_thumb_volume = volume

    @property
    def redis_volume_name(self) -> str:
        return self.resource_prefix + "redis_volume"

    @property
    def redis_volume(self) -> Optional[docker.models.volumes.Volume]:
        """
        Returns the Redis volume from attribute if it exists.
        Otherwise, falls back to finding the Redis volume with the
        same name, based on initialization settings.
        """
        volume = None
        with contextlib.suppress(AttributeError):
            volume = self._redis_volume
        if volume is None:
            volume = self._get_volume_by_name(self.redis_volume_name)
        self._redis_volume = volume
        return volume

    @redis_volume.setter
    def redis_volume(self, volume: docker.models.volumes.Volume):
        self._redis_volume = volume

    def __init__(
            self, build: str, image: str, git_url: str, git_branch: str, docker_client: docker.DockerClient,
            resource_prefix: str, port_offset: int,
            docker_api: docker.APIClient=None, logger: Optional[logging.Logger]=None,
            global_run_id: int=None, is_allow_uploads: bool=True,
    ):

        self.resource_prefix = resource_prefix
        self.port_offset = port_offset

        self.build_path = build
        self.image = image
        self.global_run_id = global_run_id
        self.git_url = git_url
        self.git_branch = git_branch
        self.docker_client = docker_client
        self.docker_api = docker_api
        if logger is None:
            logger = LOGGER
        self.logger = logger
        self.is_allow_uploads = is_allow_uploads

    @override
    def update_api_key(self, api_key: Optional[str]):
        self.lrr_api_key = api_key
        if api_key is None:
            return self._exec_redis_cli("SELECT 2\nHDEL LRR_CONFIG apikey")
        else:
            return self._exec_redis_cli(f"SELECT 2\nHSET LRR_CONFIG apikey {api_key}")

    @override
    def enable_nofun_mode(self):
        return self._exec_redis_cli("SELECT 2\nHSET LRR_CONFIG nofunmode 1")

    @override
    def disable_nofun_mode(self):
        return self._exec_redis_cli("SELECT 2\nHSET LRR_CONFIG nofunmode 0")

    @override
    def enable_lrr_debug_mode(self):
        return self._exec_redis_cli("SELECT 2\nHSET LRR_CONFIG enable_devmode 1")
    
    @override
    def disable_lrr_debug_mode(self):
        return self._exec_redis_cli("SELECT 2\nHSET LRR_CONFIG enable_devmode 0")

    # by default LRR contents directory is owned by root.
    # to make it writable by the koyomi user, we need to change the ownership.
    def allow_uploads(self):
        return self.lrr_container.exec_run(["sh", "-c", 'chown -R koyomi: content'])

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
    def setup(
        self, with_api_key: bool=False, with_nofunmode: bool=False, lrr_debug_mode: bool=False,
        test_connection_max_retries: int=4
    ):
        """
        Main entrypoint to setting up a LRR docker environment. Pulls/builds required images,
        creates/recreates required volumes, containers, networks, and connects them together,
        as well as any other configuration.

        Args:
            with_api_key: whether to add a default API key to LRR
            with_nofunmode: whether to start LRR with nofunmode on
            lrr_debug_mode: whether to start LRR with debug mode on
            test_connection_max_retries: Number of attempts to connect to the LRR server. Usually resolves after 2, unless there are many files.
        """
        # log the setup resource allocations for user to see
        # the docker image is not included, haven't decided how to classify it yet.
        self.logger.info(f"Deploying Docker LRR with the following resources: LRR container {self.lrr_container_name}, Redis container {self.redis_container_name}, LRR contents volume {self.lrr_contents_volume_name}, LRR thumb volume {self.lrr_thumb_volume_name}, redis volume {self.redis_volume_name}, network {self.network_name}")

        # >>>>> IMAGE PREPARATION >>>>>
        image_id = self.lrr_image_name
        if self.build_path:
            self.logger.info(f"Building LRR image {image_id} from build path {self.build_path}.")
            self._build_docker_image(self.build_path, force=False)
        elif self.git_url:
            self.logger.info(f"Building LRR image {image_id} from git URL {self.git_url}.")
            try:
                self.docker_client.images.get(image_id)
                self.logger.debug(f"Image {image_id} already exists, skipping build.")
            except docker.errors.ImageNotFound:
                with tempfile.TemporaryDirectory() as tmpdir:
                    self.logger.debug(f"Cloning {self.git_url} to {tmpdir}...")
                    repo_dir = Path(tmpdir) / "LANraragi"
                    repo = Repo.clone_from(self.git_url, repo_dir)
                    if self.git_branch: # throws git.exc.GitCommandError if branch does not exist.
                        repo.git.checkout(self.git_branch)
                    self._build_docker_image(repo.working_dir, force=True)
        else:
            image = DEFAULT_LANRARAGI_DOCKER_TAG
            if self.image:
                image = self.image
            self.logger.info(f"Pulling LRR image from Docker Hub: {image}.")
            self._pull_docker_image_if_not_exists(image, force=False)
            self.docker_client.images.get(image).tag(image_id)

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

        # prepare volumes
        contents_volume_name = self.lrr_contents_volume_name
        if not self.lrr_contents_volume:
            self.logger.debug(f"Creating volume: {contents_volume_name}")
            self.lrr_contents_volume = self.docker_client.volumes.create(name=contents_volume_name)
        else:
            self.logger.debug(f"Volume exists: {contents_volume_name}.")

        thumb_volume_name = self.lrr_thumb_volume_name
        if not self.lrr_thumb_volume:
            self.logger.debug(f"Creating volume: {thumb_volume_name}")
            self.lrr_thumb_volume = self.docker_client.volumes.create(name=thumb_volume_name)
        else:
            self.logger.debug(f"Volume exists: {thumb_volume_name}.")
        
        redis_volume_name = self.redis_volume_name
        if not self.redis_volume:
            self.logger.debug(f"Creating volume: {redis_volume_name}")
            self.redis_volume = self.docker_client.volumes.create(name=redis_volume_name)
        else:
            self.logger.debug(f"Volume exists: {redis_volume_name}.")

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
                hostname=redis_container_name,
                detach=True,
                network=network_name,
                ports=redis_ports,
                healthcheck=redis_healthcheck,
                volumes={
                    redis_volume_name: {"bind": "/data", "mode": "rw"}
                }
            )

        # then prepare the LRR container.
        lrr_port = self.lrr_port
        lrr_container_name = self.lrr_container_name
        lrr_contents_vol_name = self.lrr_contents_volume_name
        lrr_thumb_vol_name = self.lrr_thumb_volume_name
        lrr_ports = {
            "3000/tcp": lrr_port
        }
        lrr_environment = [
            f"LRR_REDIS_ADDRESS={redis_container_name}:{DEFAULT_REDIS_PORT}"
        ]
        create_lrr_container = False
        if self.lrr_container:
            self.logger.debug(f"LRR container exists: {self.lrr_container_name}.")
            # in this situation, whether we restart the LRR container depends on whether or not the images used for both containers
            # match.
            needs_recreate_lrr = self.lrr_container.image.id != self.docker_client.images.get(image_id).id
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
            self.lrr_container = self.docker_client.containers.create(
                image_id, hostname=lrr_container_name, name=lrr_container_name, detach=True, network=network_name, ports=lrr_ports, environment=lrr_environment,
                volumes={
                    lrr_contents_vol_name: {"bind": "/home/koyomi/lanraragi/content", "mode": "rw"},
                    lrr_thumb_vol_name: {"bind": "/home/koyomi/lanraragi/thumb", "mode": "rw"}
                }
            )
            self.logger.debug("LRR container created.")

        # start redis
        self.logger.debug(f"Starting container: {self.redis_container_name}")
        self.redis_container.start()
        self.logger.debug("Redis container started.")
        self.logger.debug("Running Redis post-startup configuration.")
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

        if lrr_debug_mode:
            resp = self.enable_lrr_debug_mode()
            if resp.exit_code  != 0:
                self._reset_docker_test_env(remove_data=True)
                raise DeploymentException(f"Failed to enable debug mode for LRR: {resp}")
        self.logger.debug("Redis server is ready.")

        # start lrr
        self.start_lrr()
        self.logger.debug("Testing connection to LRR server.")
        self.test_lrr_connection(self.lrr_port, test_connection_max_retries)
        if self.is_allow_uploads:
            resp = self.allow_uploads()
            if resp.exit_code != 0:
                self._reset_docker_test_env(remove_data=True)
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
                self._reset_docker_test_env(remove_data=True)
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
                self._reset_docker_test_env(remove_data=True)
                raise DeploymentException(f"Failed to modify permissions for LRR contents: {resp}")
        self.logger.debug("LRR server is ready.")

    @override
    def teardown(self, remove_data: bool=False):
        self._reset_docker_test_env(remove_data=remove_data)
        self.logger.info("Cleanup complete.")

    def _get_container_by_name(self, container_name: str) -> Optional[docker.models.containers.Container]:
        """
        Tries to return a container DTO by its name if exists. Otherwise, returnes None.
        """
        with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
            container = self.docker_client.containers.get(container_name)
            return container
        return None
    
    def _get_volume_by_name(self, volume_name: str) -> Optional[docker.models.volumes.Volume]:
        """
        Tries to return a volume DTO by its name if exists. Otherwise, returnes None.
        """
        with contextlib.suppress(docker.errors.NotFound, docker.errors.APIError):
            container = self.docker_client.volumes.get(volume_name)
            return container
        return None
    
    def _get_network_by_name(self, network_name: str) -> Optional[docker.models.networks.Network]:
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

    def _reset_docker_test_env(self, remove_data: bool=False):
        """
        Reset docker test environment (LRR and Redis containers, testing network) between tests.
        Stops containers, then removes them. Then, removes the data (if applied). Finally removes
        the network.
        
        If something goes wrong during setup, the environment will be reset and the data should be removed.
        """
        if self.lrr_container:
            self.lrr_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.lrr_container_name}")
        if self.redis_container:
            self.redis_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.redis_container_name}")
        if self.lrr_container:
            self.lrr_container.remove(force=True)
            self.logger.debug(f"Removed container: {self.lrr_container_name}")
        if self.redis_container:
            self.redis_container.remove(force=True)
            self.logger.debug(f"Removed container: {self.redis_container_name}")
        if remove_data:
            if self.lrr_contents_volume:
                self.lrr_contents_volume.remove(force=True)
                self.logger.debug(f"Removed volume: {self.lrr_contents_volume_name}")
            if self.lrr_thumb_volume:
                self.lrr_thumb_volume.remove(force=True)
                self.logger.debug(f"Removed volume: {self.lrr_thumb_volume_name}")
            if self.redis_volume:
                self.redis_volume.remove(force=True)
                self.logger.debug(f"Removed volume: {self.redis_volume_name}")

        if hasattr(self, 'network') and self.network:
            self.network.remove()
            self.logger.debug(f"Removed network: {self.network_name}")

    def _build_docker_image(self, build_path: Path, force: bool=False):
        """
        Build a docker image.

        Args:
            build_path: The path to the build directory.
            force: Whether to force the build (e.g. even if the image already exists).
        """
        image_id = self.lrr_image_name

        if force:
            if not Path(build_path).exists():
                raise FileNotFoundError(f"Build path {build_path} does not exist!")
            dockerfile_path = Path(build_path) / "tools" / "build" / "docker" / "Dockerfile"
            if not dockerfile_path.exists():
                raise FileNotFoundError(f"Dockerfile {dockerfile_path} does not exist!")
            self.logger.debug(f"Building LRR image; this can take a while ({dockerfile_path}).")
            build_start = time.time()
            if self.docker_api:
                for lineb in self.docker_api.build(path=build_path, dockerfile=dockerfile_path, tag=image_id):
                    if (data := json.loads(lineb.decode('utf-8').strip())) and (stream := data.get('stream')):
                        self.logger.debug(stream.strip())
            else:
                self.docker_client.images.build(path=build_path, dockerfile=dockerfile_path, tag=image_id)
            build_time = time.time() - build_start
            self.logger.info(f"LRR image {image_id} build complete: time {build_time}s")
            return
        else:
            try:
                self.docker_client.images.get(image_id)
                self.logger.debug(f"Image {image_id} already exists, skipping build.")
                return
            except docker.errors.ImageNotFound:
                self.logger.debug(f"Image {image_id} not found, building.")
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
            self.logger.debug(f"Checking if {image} exists.")
            try:
                self.docker_client.images.get(image)
                self.logger.debug(f"{image} already exists, skipping pull.")
                return
            except docker.errors.ImageNotFound:
                self.logger.debug(f"{image} not found, pulling.")
                self.docker_client.images.pull(image)
                return
