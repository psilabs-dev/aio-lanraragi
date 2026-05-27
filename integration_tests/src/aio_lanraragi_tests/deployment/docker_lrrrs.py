"""
Docker deployment context for LRRRS (Rust rewrite of LRR).

LRRRS replaces Perl LRR and uses Postgres in place of Redis. This context
extends the Postgres-alongside-Redis deployment by:

- defaulting the Dockerfile to ``tools/build/docker/lrrrs.Dockerfile``
- injecting the ``LRR_POSTGRES_USER``/``LRR_POSTGRES_PASSWORD``/``LRR_POSTGRES_DB``
  environment variables the LRRRS binary expects
- using test-harness credentials (``postgres`` / ``postgres`` / ``lanraragi``); the standalone compose file uses ``lanraragi/lanraragi/lanraragi`` instead.
- overriding all Redis-backed config writes (api key, nofunmode, CORS, etc.)
  to write directly to the Postgres ``lrr_config`` and ``lrr_api_key`` tables
  that LRRRS reads at boot time.
"""

import asyncio
import concurrent.futures
import logging
import types
from pathlib import Path
from typing import override

import docker

from aio_lanraragi_tests.common import DEFAULT_API_KEY
from aio_lanraragi_tests.deployment.base import PluginPathsT
from aio_lanraragi_tests.deployment.docker import DockerLRRCacheBackend
from aio_lanraragi_tests.deployment.docker_postgres import (
    DockerPostgresLRRDeploymentContext,
)

LRRRS_DOCKERFILE_RELPATH = Path("tools/build/docker/lrrrs.Dockerfile")
# Must match the parent DockerPostgresLRRDeploymentContext.setup() Postgres
# container provisioning credentials. The LRRRS compose file uses
# "lanraragi/lanraragi/lanraragi" but that's standalone; the test harness
# follows the perl-side dev-postgresql/main convention.
LRRRS_POSTGRES_USER = "postgres"
LRRRS_POSTGRES_PASSWORD = "postgres"
LRRRS_POSTGRES_DB = "lanraragi"

LOGGER = logging.getLogger(__name__)


class DockerLrrrsLRRDeploymentContext(DockerPostgresLRRDeploymentContext):
    """
    Docker LRRRS deployment with a Postgres peer container. Config writes
    (api key, nofunmode, CORS, progress flags, pagesize) go directly to
    Postgres via ``_pg_execute``; no Redis intermediary.
    """

    def __init__(
        self, build: str, image: str, git_url: str, git_ref: str,
        docker_client: docker.DockerClient, staging_dir: str,
        resource_prefix: str, port_offset: int,
        build_ref: str = None, dockerfile: str = None,
        docker_api: docker.APIClient | None = None,
        logger: logging.Logger | None = None,
        global_run_id: int = None, is_allow_uploads: bool = True,
        is_force_build: bool = False,
        cache_backend: DockerLRRCacheBackend = DockerLRRCacheBackend.REDIS,
        postgres_jit: bool = True,
        postgres_shared_buffers_mb: int = 128,
        postgres_work_mem_mb: int = 4,
    ):
        # Default the build to use lrrrs.Dockerfile unless one was explicitly provided.
        if dockerfile is None and build is not None:
            candidate = Path(build) / LRRRS_DOCKERFILE_RELPATH
            if candidate.exists():
                dockerfile = str(candidate)
        super().__init__(
            build, image, git_url, git_ref, docker_client, staging_dir,
            resource_prefix, port_offset,
            build_ref=build_ref, dockerfile=dockerfile, docker_api=docker_api,
            logger=logger, global_run_id=global_run_id,
            is_allow_uploads=is_allow_uploads, is_force_build=is_force_build,
            cache_backend=cache_backend,
            postgres_jit=postgres_jit,
            postgres_shared_buffers_mb=postgres_shared_buffers_mb,
            postgres_work_mem_mb=postgres_work_mem_mb,
        )

    @property
    @override
    def has_mojo_logs(self) -> bool:
        # LRRRS uses no Mojolicious logging.
        return False

    # --- Public methods ------------------------------------------------------

    @override
    def allow_uploads(self):
        # LRRRS does not manage /home/koyomi/lanraragi content/thumb paths;
        # there is nothing to chmod. The parent setup pipeline inspects
        # `resp.exit_code` after this call, so return a stub that reports
        # success rather than None.
        # TODO: verify that the koyomi user has write access to the bind-mounted content path; the stub currently returns success unconditionally.
        return types.SimpleNamespace(exit_code=0, output=b"")

    @override
    def update_api_key(self, api_key: str | None):
        """
        Replace the singleton API key row in ``lrr_api_key``.

        Deletes any existing row, then inserts a new Argon2id hash when
        ``api_key`` is not None.  Maintains ``self.lrr_api_key`` bookkeeping
        so callers can read the plaintext key back as usual.
        """
        import argon2
        self.lrr_api_key = api_key
        self._pg_execute("DELETE FROM lrr_api_key")
        if api_key is not None:
            ph = argon2.PasswordHasher()
            key_hash = ph.hash(api_key)
            self._pg_execute(
                "INSERT INTO lrr_api_key (key_hash) VALUES ($1)",
                key_hash,
            )

    @override
    def enable_nofun_mode(self):
        self._pg_execute("UPDATE lrr_config SET nofunmode = $1 WHERE id = 1", True)

    @override
    def disable_nofun_mode(self):
        self._pg_execute("UPDATE lrr_config SET nofunmode = $1 WHERE id = 1", False)

    @override
    def enable_cors(self):
        self._pg_execute("UPDATE lrr_config SET enablecors = $1 WHERE id = 1", True)

    @override
    def disable_cors(self):
        self._pg_execute("UPDATE lrr_config SET enablecors = $1 WHERE id = 1", False)

    @override
    def enable_auth_progress(self):
        self._pg_execute("UPDATE lrr_config SET authprogress = $1 WHERE id = 1", True)

    @override
    def disable_auth_progress(self):
        self._pg_execute("UPDATE lrr_config SET authprogress = $1 WHERE id = 1", False)

    @override
    def enable_local_progress(self):
        self._pg_execute("UPDATE lrr_config SET localprogress = $1 WHERE id = 1", True)

    @override
    def disable_local_progress(self):
        self._pg_execute("UPDATE lrr_config SET localprogress = $1 WHERE id = 1", False)

    @override
    def enable_openapi_bypass(self):
        self._pg_execute("UPDATE lrr_config SET disableopenapi = $1 WHERE id = 1", True)

    @override
    def disable_openapi_bypass(self):
        self._pg_execute("UPDATE lrr_config SET disableopenapi = $1 WHERE id = 1", False)

    @override
    def set_pagesize(self, pagesize: int):
        self._pg_execute("UPDATE lrr_config SET pagesize = $1 WHERE id = 1", pagesize)

    @override
    def set_excluded_namespaces(self, namespaces: str):
        """LRRRS reads excluded_namespaces from lrr_config on each getStatistics and getServerInfo request (no Redis, no restart required)."""
        self._pg_execute(
            "UPDATE lrr_config SET excluded_namespaces = $1 WHERE id = 1",
            namespaces,
        )

    @override
    def enable_metrics(self):
        # No ``metricsenabled`` column in ``lrr_config`` yet; this is a no-op
        # until a follow-up migration adds the column.
        LOGGER.warning(
            "enable_metrics: no-op under LRRRS; lrr_config has no metricsenabled "
            "column yet; a follow-up migration is required."
        )

    @override
    def disable_metrics(self):
        # Same gap as enable_metrics; no-op with explanation.
        LOGGER.warning(
            "disable_metrics: no-op under LRRRS; lrr_config has no metricsenabled "
            "column yet; a follow-up migration is required."
        )

    # --- Lifecycle methods --------------------------------------------------

    @override
    def setup(
        self, with_api_key: bool = False, with_nofunmode: bool = False,
        enable_cors: bool = False, lrr_debug_mode: bool = False,
        environment: dict[str, str] = {}, plugin_paths: PluginPathsT = {},
        test_connection_max_retries: int = 4,
    ):
        # inject LRRRS-specific env vars.
        # LRR_CONTENT_DIR aligns LRRRS's content path with the bind mount the
        # parent harness uses (/home/koyomi/lanraragi/content).  Without this,
        # LRRRS defaults to ./content relative to its WORKDIR
        # (/home/koyomi/lrrrs), writing files outside the mounted volume.
        lrrrs_pg_env = {
            "LRR_POSTGRES_USER": LRRRS_POSTGRES_USER,
            "LRR_POSTGRES_PASSWORD": LRRRS_POSTGRES_PASSWORD,
            "LRR_POSTGRES_DB": LRRRS_POSTGRES_DB,
            "LRR_CONTENT_DIR": "/home/koyomi/lanraragi/content",
        }
        merged_env = {**lrrrs_pg_env, **environment}
        # The parent's Perl-flavored setup writes config to Redis BEFORE
        # starting LRR. LRRRS's config tables (lrr_api_key, lrr_config) only
        # exist after LRRRS's sqlx migrations run at startup, so we must defer
        # the config writes until after super().setup() has brought LRRRS up.
        # Pass False for all config flags here, then re-apply via our Postgres
        # overrides below. CORS reconfig requires a restart since LRRRS reads
        # lrr_config once at boot into AppState.
        super().setup(
            with_api_key=False, with_nofunmode=False,
            enable_cors=False, lrr_debug_mode=lrr_debug_mode,
            environment=merged_env, plugin_paths=plugin_paths,
            test_connection_max_retries=test_connection_max_retries,
        )
        # All three config writes require a restart: LRRRS reads lrr_api_key
        # and lrr_config once at boot into AppState. Any value
        # we change here is invisible until the next restart.
        need_restart = False
        if with_api_key:
            self.update_api_key(DEFAULT_API_KEY)
            need_restart = True
        if with_nofunmode:
            self.enable_nofun_mode()
            need_restart = True
        if enable_cors:
            self.enable_cors()
            need_restart = True
        if need_restart:
            self.restart()

    @override
    def teardown(self, remove_data: bool = False):
        # No pool to close; see _pg_execute for the per-call connection
        # rationale. teardown remains overridable for future cleanup hooks.
        super().teardown(remove_data=remove_data)

    # --- Private methods ----------------------------------------------------

    def _pg_execute(self, query: str, *args):
        """
        Run a single parameterised Postgres statement against the LRRRS DB.

        Sync API for the base class. asyncpg is async-only, so we drive the
        coroutine via asyncio.run on a *worker thread*; calling asyncio.run
        on the test thread fails when pytest-asyncio's event loop is already
        running (which it is, since base-class setup runs from inside an
        @pytest.mark.asyncio test). The worker thread has its own thread-local
        asyncio state, so it can create+destroy a loop freely.

        Each call uses a fresh connection rather than a pool: an asyncpg pool
        is bound to the event loop that created it, and our worker thread
        creates a new loop per call, so a cached pool would be unusable across
        calls. Per-call connection cost (~10ms) is acceptable for test config
        writes that happen a handful of times per test.
        """
        import asyncpg

        async def _run():
            conn = await asyncpg.connect(
                host="127.0.0.1",
                port=self.postgres_port,
                user=LRRRS_POSTGRES_USER,
                password=LRRRS_POSTGRES_PASSWORD,
                database=LRRRS_POSTGRES_DB,
            )
            try:
                await conn.execute(query, *args)
            finally:
                await conn.close()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(lambda: asyncio.run(_run())).result()
        except asyncpg.exceptions.UndefinedTableError:
            # LRRRS migrations create lrr_api_key and lrr_config on first boot;
            # writes issued before that boot (e.g. parent's unconditional
            # disable_cors/disable_openapi_bypass called pre-start_lrr) hit an
            # empty schema. Silently no-op: the migration pre-inserts default
            # rows that match what these writes would set, and any non-default
            # flag is re-applied via setup()'s deferred-config block.
            LOGGER.debug(f"LRRRS schema not yet present; skipping: {query}")
