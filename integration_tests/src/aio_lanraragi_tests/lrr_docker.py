"""
Python module for setting up and tearing down docker environments for LANraragi.
"""

import contextlib
import json
import logging
from pathlib import Path
import tempfile
import time
from typing import Optional
import docker
import docker.errors
import docker.models
import docker.models.containers
import docker.models.networks
from git import Repo
import requests

class DockerTestException(Exception):
    pass

DEFAULT_REDIS_TAG = "redis:7.2.4"
DEFAULT_LANRARAGI_TAG = "difegue/lanraragi"
DEFAULT_NETWORK_NAME = "lanraragi-integration-test-network"

LOGGER = logging.getLogger(__name__)

class LRREnvironment:

    """
    Set up a containerized LANraragi environment with Docker.
    This can be used in a pytest function and provided as a fixture.
    """
    
    def __init__(
            self, build: str, image: str, git_url: str, git_branch: str, docker_client: docker.DockerClient, 
            docker_api: docker.APIClient=None, logger: Optional[logging.Logger]=None,
            lrr_port: int=3001
    ):
        self.build_path = build
        self.image = image
        self.git_url = git_url
        self.git_branch = git_branch
        self.docker_client = docker_client
        self.docker_api = docker_api
        self.redis_container: docker.models.containers.Container = None
        self.lrr_container: docker.models.containers.Container = None
        if logger is None:
            logger = logging.getLogger(__name__)
        self.logger = logger
        self.lrr_port = lrr_port

    def reset_docker_test_env(self):
        """
        Reset docker test environment (LRR and Redis containers, testing network) if something
        goes wrong during setup.
        """
        with contextlib.suppress(docker.errors.NotFound):
            self.docker_client.containers.get(self.redis_container.id).stop(timeout=3)
        with contextlib.suppress(docker.errors.NotFound):
            self.docker_client.containers.get(self.lrr_container.id).stop(timeout=3)
        with contextlib.suppress(docker.errors.NotFound):
            self.docker_client.networks.get(self.network.id).remove()
        with contextlib.suppress(docker.errors.NotFound):
            self.docker_client.images.get("lanraragi-integration-test").remove(force=True)

    def build_docker_image(self, build_path: Path):
        if not Path(build_path).exists():
            raise FileNotFoundError(f"Build path {build_path} does not exist!")
        dockerfile_path = Path(build_path) / "tools" / "build" / "docker" / "Dockerfile"
        if not dockerfile_path.exists():
            raise FileNotFoundError(f"Dockerfile {dockerfile_path} does not exist!")
        self.logger.info(f"Building LRR image; this can take a while ({dockerfile_path}).")
        if self.docker_api:
            for lineb in self.docker_api.build(path=build_path, dockerfile=dockerfile_path, tag='lanraragi-integration-test'):
                if (data := json.loads(lineb.decode('utf-8').strip())) and (stream := data.get('stream')):
                    self.logger.info(stream.strip())
        else:
            self.docker_client.images.build(path=build_path, dockerfile=dockerfile_path, tag='lanraragi-integration-test')

    def add_api_key(self):
        return self.redis_container.exec_run(["bash", "-c", 'redis-cli <<EOF\nSELECT 2\nHSET LRR_CONFIG apikey lanraragi\nEOF'])

    def enable_nofun_mode(self):
        return self.redis_container.exec_run(["bash", "-c", 'redis-cli <<EOF\nSELECT 2\nHSET LRR_CONFIG nofunmode 1\nEOF'])

    def disable_nofun_mode(self):
        return self.redis_container.exec_run(["bash", "-c", 'redis-cli <<EOF\nSELECT 2\nHSET LRR_CONFIG nofunmode 0\nEOF'])
    
    def allow_uploads(self):
        return self.lrr_container.exec_run(["sh", "-c", 'chown -R koyomi: content'])

    def setup(self):
        # prepare images
        if self.build_path:
            self.build_docker_image(self.build_path)
        elif self.git_url:
            self.logger.info(f"Cloning from {self.git_url}...")
            with tempfile.TemporaryDirectory() as tmpdir:
                repo_dir = Path(tmpdir) / "LANraragi"
                repo = Repo.clone_from(self.git_url, repo_dir)
                if self.git_branch: # throws git.exc.GitCommandError if branch does not exist.
                    repo.git.checkout(self.git_branch)
                self.build_docker_image(repo.working_dir)
        else:
            image = DEFAULT_LANRARAGI_TAG
            if self.image:
                image = self.image
            self.logger.debug(f"Pulling {image}.")
            self.docker_client.images.pull(image)
            self.docker_client.images.get(image).tag("lanraragi-integration-test")

        # check testing environment availability
        # raise a testing exception if these conditions are violated.
        container: docker.models.containers.Container
        for container in self.docker_client.containers.list(all=True):
            if container_name := container.name in {'test-lanraragi', 'test-redis'}:
                raise DockerTestException(f"Container {container_name} exists!")
        network: docker.models.networks.Network
        for network in self.docker_client.networks.list():
            if network_name := network.name == DEFAULT_NETWORK_NAME:
                raise DockerTestException(f"Network {network_name} exists!")

        # pull redis
        self.docker_client.images.pull(DEFAULT_REDIS_TAG)
        self.logger.info("Creating test network.")
        network = self.docker_client.networks.create(DEFAULT_NETWORK_NAME, driver="bridge")
        self.network = network

        # create containers
        self.logger.info("Creating containers.")
        redis_healthcheck = {
            "test": [ "CMD", "redis-cli", "--raw", "incr", "ping" ],
            "start_period": 1000000 * 1000 # 1s
        }
        self.redis_container = self.docker_client.containers.create(
            DEFAULT_REDIS_TAG, name="test-redis", hostname="test-redis", detach=True, network=DEFAULT_NETWORK_NAME, healthcheck=redis_healthcheck, auto_remove=True
        )

        lrr_ports = {
            "3000/tcp": self.lrr_port
        }
        lrr_environment = [
            "LRR_REDIS_ADDRESS=test-redis:6379"
        ]
        self.lrr_container = self.docker_client.containers.create(
            "lanraragi-integration-test", hostname="test-lanraragi", name="test-lanraragi", detach=True, network=DEFAULT_NETWORK_NAME, ports=lrr_ports, environment=lrr_environment, auto_remove=True
        )

        # start containers
        self.logger.info("Starting containers.")
        self.redis_container.start()
        self.lrr_container.start()

        # post startup
        # wait until LRR server is set up.
        self.logger.info("Testing connection to LRR server.")
        retry_count = 0
        while True:
            try:
                resp = requests.get(f"http://127.0.0.1:{self.lrr_port}")
                if resp.status_code != 200:
                    self.reset_docker_test_env()
                    raise DockerTestException(f"Response status code is not 200: {resp.status_code}", returncode=1)
                else:
                    break
            except requests.exceptions.ConnectionError:
                if retry_count < 8:
                    time_to_sleep = 2 ** (retry_count + 1)
                    self.logger.debug(f"Could not reach LRR server; retrying after {time_to_sleep}s.")
                    retry_count += 1
                    time.sleep(time_to_sleep)
                    continue
                else:
                    self.reset_docker_test_env()
                    raise DockerTestException("Failed to connect to the LRR server!", returncode=1)

        self.logger.debug("Running post-startup configuration.")
        resp = self.add_api_key()
        if resp.exit_code != 0:
            self.reset_docker_test_env()
            raise DockerTestException(f"Failed to add API key to server: {resp}")
        resp = self.enable_nofun_mode()
        if resp.exit_code != 0:
            self.reset_docker_test_env()
            raise DockerTestException(f"Failed to enable nofunmode: {resp}")
        resp = self.allow_uploads()
        if resp.exit_code != 0:
            self.reset_docker_test_env()
            raise DockerTestException(f"Failed to modify permissions for LRR contents: {resp}")

        self.logger.info("Environment setup complete, proceeding to testing...")

    def teardown(self):
        self.reset_docker_test_env()
        self.logger.info("Cleanup complete.")