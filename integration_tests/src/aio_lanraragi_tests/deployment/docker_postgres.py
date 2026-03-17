"""
Docker deployment context with PostgreSQL support.

Extends the base Docker deployment to add a PostgreSQL container alongside
Redis and LRR. Redis is still used for config, cache, locks, and filemap;
PostgreSQL handles archive/category/tankoubon metadata and search indexing.

PostgreSQL data is stored in a Docker named volume (not a bind mount) to
avoid VirtioFS permission coherency failures on macOS that cause pg_wal
PANIC under concurrent write load.
"""

import contextlib
import logging
import time
from typing import override

import docker.models.containers
import docker.models.volumes

from aio_lanraragi_tests.common import DEFAULT_POSTGRES_PORT
from aio_lanraragi_tests.deployment.base import PluginPathsT
from aio_lanraragi_tests.deployment.docker import (
    DockerLRRCacheBackend,
    DockerLRRDeploymentContext,
)
from aio_lanraragi_tests.exceptions import DeploymentException

DEFAULT_POSTGRES_DOCKER_TAG = "postgres:17.4"

LOGGER = logging.getLogger(__name__)


class DockerPostgresLRRDeploymentContext(DockerLRRDeploymentContext):
    """
    Docker LRR deployment with an additional PostgreSQL container.

    PostgreSQL is created on the shared bridge network before the parent
    setup runs, so LRR receives ``LRR_POSTGRES_HOST`` and
    ``LRR_POSTGRES_PORT`` environment variables at container creation time.
    """

    @property
    def postgres_container_name(self) -> str:
        return self.resource_prefix + "postgres_service"

    @property
    def postgres_container(self) -> docker.models.containers.Container | None:
        container = None
        with contextlib.suppress(AttributeError):
            container = self._postgres_container
        if container is None:
            container = self._get_container_by_name(self.postgres_container_name)
        self._postgres_container = container
        return container

    @postgres_container.setter
    def postgres_container(self, container: docker.models.containers.Container):
        self._postgres_container = container

    @property
    def postgres_port(self) -> int:
        return DEFAULT_POSTGRES_PORT + self.port_offset

    @property
    def postgres_volume_name(self) -> str:
        return self.resource_prefix + "postgres_data"

    @property
    def postgres_volume(self) -> docker.models.volumes.Volume | None:
        return self._get_volume_by_name(self.postgres_volume_name)

    @property
    def postgres_jit(self) -> bool:
        """
        Whether PostgreSQL JIT compilation is enabled (recommend enabled).

        JIT compiles query expressions into native code at execution time.
        At large scale (100K+ archives), JIT amortizes its upfront compilation
        cost (~80-125ms) through faster execution of complex queries involving
        tag filters, correlated subqueries, and multi-table joins. At small
        scale (<10K archives), JIT overhead dominates query execution time
        and should be disabled for accurate profiling.
        """
        return self._postgres_jit

    @property
    def postgres_shared_buffers_mb(self) -> int:
        """PostgreSQL shared_buffers size in MB.

        Controls the amount of memory dedicated to caching table and index data.
        Default Postgres value is 128MB. For datasets with 100K+ archives,
        256-512MB allows the full working set (archive table, tag map, indexes)
        to stay resident in shared buffers, eliminating disk reads after warmup.
        """
        return self._postgres_shared_buffers_mb

    @property
    def postgres_work_mem_mb(self) -> int:
        """PostgreSQL work_mem size in MB.

        Controls per-operation memory for sorts and hash aggregates. Default
        Postgres value is 4MB. Search queries with large result sets (sorting
        100K titles with ICU collation, hash aggregating tag maps) spill to
        disk at 4MB. 16-32MB keeps these operations in memory.
        """
        return self._postgres_work_mem_mb

    def __init__(
        self, build: str, image: str, git_url: str, git_ref: str,
        docker_client, staging_dir: str,
        resource_prefix: str, port_offset: int,
        build_ref: str = None, dockerfile: str = None,
        docker_api=None, logger: logging.Logger | None = None,
        global_run_id: int = None, is_allow_uploads: bool = True,
        is_force_build: bool = False,
        cache_backend: DockerLRRCacheBackend = DockerLRRCacheBackend.REDIS,
        postgres_jit: bool = True,
        postgres_shared_buffers_mb: int = 128,
        postgres_work_mem_mb: int = 4,
    ):
        super().__init__(
            build, image, git_url, git_ref, docker_client, staging_dir,
            resource_prefix, port_offset,
            build_ref=build_ref, dockerfile=dockerfile, docker_api=docker_api,
            logger=logger, global_run_id=global_run_id,
            is_allow_uploads=is_allow_uploads, is_force_build=is_force_build,
            cache_backend=cache_backend,
        )
        self._postgres_jit = postgres_jit
        self._postgres_shared_buffers_mb = postgres_shared_buffers_mb
        self._postgres_work_mem_mb = postgres_work_mem_mb

    def get_postgres_logs(self, tail: int = 100) -> bytes:
        if self.postgres_container:
            return self.postgres_container.logs(tail=tail)
        self.logger.warning("Postgres container not available for log extraction")
        return b"No Postgres container available"

    def test_postgres_connection(self, max_retries: int = 4):
        """Wait for the Postgres container healthcheck to report healthy."""
        self.logger.debug("Waiting for Postgres to be ready...")
        retry_count = 0
        while True:
            self.postgres_container.reload()
            health = self.postgres_container.attrs.get("State", {}).get("Health", {}).get("Status")
            if health == "healthy":
                break
            if retry_count >= max_retries:
                raise DeploymentException(f"Postgres container failed to become healthy (status: {health})!")
            time_to_sleep = 2 ** (retry_count + 1)
            self.logger.debug(f"Postgres not ready (status: {health}). Retry in {time_to_sleep}s ({retry_count+1}/{max_retries})...")
            retry_count += 1
            time.sleep(time_to_sleep)

    def start_postgres(self):
        return self.postgres_container.start()

    def stop_postgres(self, timeout: int = 10):
        if self.postgres_container:
            self.postgres_container.stop(timeout=timeout)

    @override
    def setup(
        self, with_api_key: bool = False, with_nofunmode: bool = False,
        enable_cors: bool = False, lrr_debug_mode: bool = False,
        environment: dict[str, str] = {}, plugin_paths: PluginPathsT = {},
        test_connection_max_retries: int = 4,
    ):
        # create postgres named volume (avoids VirtioFS bind mount issues on macOS)
        volume_name = self.postgres_volume_name
        if not self.postgres_volume:
            self.logger.debug(f"Creating Postgres volume: {volume_name}")
            self.docker_client.volumes.create(volume_name)
        else:
            self.logger.debug(f"Postgres volume exists: {volume_name}")

        # pull postgres image
        self._pull_docker_image_if_not_exists(DEFAULT_POSTGRES_DOCKER_TAG, force=False)

        # pre-create network so postgres container can join it
        if not self.network:
            self.logger.debug(f"Creating network: {self.network_name}.")
            self.network = self.docker_client.networks.create(self.network_name, driver="bridge")

        # create postgres container
        if not self.postgres_container:
            jit_value = "on" if self.postgres_jit else "off"
            self.logger.debug(
                f"Creating postgres container: {self.postgres_container_name} "
                f"(jit={jit_value}, shared_buffers={self.postgres_shared_buffers_mb}MB, "
                f"work_mem={self.postgres_work_mem_mb}MB)"
            )
            # shm_size must accommodate shared_buffers + overhead for parallel workers.
            # Docker defaults to 64MB which is insufficient when shared_buffers > 64MB.
            shm_size_mb = max(128, self.postgres_shared_buffers_mb * 2)
            self.postgres_container = self.docker_client.containers.create(
                DEFAULT_POSTGRES_DOCKER_TAG,
                name=self.postgres_container_name,
                hostname=self.postgres_container_name,
                detach=True,
                network=self.network_name,
                command=[
                    "postgres",
                    "-c", f"jit={jit_value}",
                    "-c", f"shared_buffers={self.postgres_shared_buffers_mb}MB",
                    "-c", f"work_mem={self.postgres_work_mem_mb}MB",
                ],
                shm_size=f"{shm_size_mb}m",
                ports={"5432/tcp": self.postgres_port},
                healthcheck={
                    "test": ["CMD-SHELL", "pg_isready -U postgres"],
                    "start_period": 2_000_000_000,
                },
                volumes={
                    volume_name: {"bind": "/var/lib/postgresql/data", "mode": "rw"},
                },
                environment=[
                    "POSTGRES_USER=postgres",
                    "POSTGRES_PASSWORD=postgres",
                    "POSTGRES_DB=lanraragi",
                ],
            )
        else:
            self.logger.debug(f"Postgres container exists: {self.postgres_container_name}.")

        # start postgres and verify
        self.start_postgres()
        self.test_postgres_connection(max_retries=test_connection_max_retries)
        self.logger.debug("Postgres container started and healthy.")

        # inject postgres env vars, then delegate everything else to parent
        pg_env = {
            "LRR_POSTGRES_HOST": self.postgres_container_name,
            "LRR_POSTGRES_PORT": str(DEFAULT_POSTGRES_PORT),
        }
        merged_env = {**pg_env, **environment}
        super().setup(
            with_api_key=with_api_key, with_nofunmode=with_nofunmode,
            enable_cors=enable_cors, lrr_debug_mode=lrr_debug_mode,
            environment=merged_env, plugin_paths=plugin_paths,
            test_connection_max_retries=test_connection_max_retries,
        )

    @override
    def start(self, test_connection_max_retries: int = 4):
        self.start_postgres()
        self.test_postgres_connection()
        super().start(test_connection_max_retries=test_connection_max_retries)

    @override
    def stop(self):
        super().stop()
        self.stop_postgres(timeout=1)
        self.logger.debug(f"Stopped container: {self.postgres_container_name}")

    @override
    def restart(self):
        # stop all
        if self.lrr_container:
            self.lrr_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.lrr_container_name}")
        if self.redis_container:
            self.redis_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.redis_container_name}")
        self.stop_postgres(timeout=1)
        self.logger.debug(f"Stopped container: {self.postgres_container_name}")

        # start postgres first
        self.start_postgres()
        self.test_postgres_connection()

        # start redis
        self.logger.debug(f"Starting container: {self.redis_container_name}")
        self.redis_container.start()
        self.logger.debug("Redis container started.")

        # start LRR
        self.start_lrr()
        self.logger.debug("Testing connection to LRR server.")
        self.test_lrr_connection(self.lrr_port)
        if self.is_allow_uploads:
            resp = self.allow_uploads()
            if resp.exit_code != 0:
                raise DeploymentException(f"Failed to modify permissions for LRR contents: {resp}")
        self.logger.debug("LRR server is ready.")

    @override
    def _reset_docker_test_env(self, remove_data: bool = False):
        # clean up postgres container before parent handles redis/LRR/network
        if self.postgres_container:
            self.postgres_container.stop(timeout=1)
            self.logger.debug(f"Stopped container: {self.postgres_container_name}")
            self.postgres_container.remove(v=True, force=True)
            self.logger.debug(f"Removed container: {self.postgres_container_name}")

        if remove_data:
            volume = self.postgres_volume
            if volume:
                volume.remove(force=True)
                self.logger.debug(f"Removed postgres volume: {self.postgres_volume_name}")

        super()._reset_docker_test_env(remove_data=remove_data)
