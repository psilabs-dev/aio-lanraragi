"""
Homebrew (macOS/Linuxbrew) LRR deployment: builds the formula into a content-hash
keg (the session-shared artifact) and runs the keg's perl/redis as native processes.
"""

import contextlib
import hashlib
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import override

import redis

from aio_lanraragi_tests.common import DEFAULT_API_KEY
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    PluginPathsT,
)
from aio_lanraragi_tests.exceptions import DeploymentException

LOGGER = logging.getLogger(__name__)

class HomebrewLRRDeploymentContext(AbstractLRRDeploymentContext):
    """Set up a LANraragi environment via Homebrew (macOS or Linuxbrew)."""

    @override
    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @override
    @property
    def archives_dir(self) -> Path:
        contents_dirname = self.resource_prefix + "archives"
        return self.staging_dir / contents_dirname

    @property
    def thumb_dir(self) -> Path:
        thumb_dirname = self.resource_prefix + "thumb"
        return self.staging_dir / thumb_dirname

    @override
    @property
    def logs_dir(self) -> Path:
        logs_dir = self.resource_prefix + "log"
        return self.staging_dir / logs_dir

    @property
    def temp_dir(self) -> Path:
        temp_dir = self.resource_prefix + "temp"
        return self.staging_dir / temp_dir

    @property
    def overlay_dir(self) -> Path:
        """
        Per-module Perl include directory prepended to PERL5LIB, holding test plugins so
        they are discovered without mutating the shared keg.
        """
        overlay_dir = self.resource_prefix + "plugins"
        return self.staging_dir / overlay_dir

    @property
    def lrr_plugin_overlay_dir(self) -> Path:
        return self.overlay_dir / "LANraragi" / "Plugin"

    @property
    def lrr_log_path(self) -> Path:
        return self.logs_dir / "lanraragi.log"

    @property
    def lrr_stdout_path(self) -> Path:
        return self.logs_dir / "lanraragi-stdout.log"

    @property
    def redis_log_path(self) -> Path:
        return self.logs_dir / "redis.log"

    @property
    def build_path(self) -> Path:
        return self._build_path

    @property
    def build_ref(self) -> str | None:
        """
        Optional committed git ref to build instead of the working tree.
        """
        return self._build_ref

    @property
    def keg_dir(self) -> Path:
        """Resolved Homebrew keg directory for the installed lanraragi version."""
        if self._keg_dir is None:
            raise DeploymentException("Keg directory requested before the keg was installed.")
        return self._keg_dir

    @property
    def keg_libexec(self) -> Path:
        return self.keg_dir / "libexec"

    @property
    def keg_perl5lib(self) -> Path:
        return self.keg_libexec / "lib" / "perl5"

    @property
    def keg_redis_conf(self) -> Path:
        return self.keg_libexec / "redis.conf"

    @property
    def lrr_launcherpl_path(self) -> Path:
        return self.keg_libexec / "script" / "launcher.pl"

    @property
    def lrr_lanraragi_path(self) -> Path:
        return self.keg_libexec / "script" / "lanraragi"

    @property
    def perl_exe_path(self) -> Path:
        """
        Path to brew's perl, the perl the keg's XS modules were built against.
        """
        return self._brew_prefix("perl") / "bin" / "perl"

    @property
    def db_server_exe(self) -> Path:
        """
        Path to the DB server binary selected by --cache-backend (redis-server or valkey-server).
        """
        if self._cache_backend in ("valkey", "valkey8"):
            return self._brew_prefix("valkey") / "bin" / "valkey-server"
        return self._brew_prefix("redis") / "bin" / "redis-server"

    @property
    def redis_client(self) -> redis.Redis:
        if not hasattr(self, "_redis_client") or not self._redis_client:
            self._redis_client = redis.Redis(host="127.0.0.1", port=self.redis_port, decode_responses=True)
        return self._redis_client

    @property
    def lrr_pid(self) -> int | None:
        return _get_port_owner_pid(self.lrr_port)

    @property
    def redis_pid(self) -> int | None:
        return _get_port_owner_pid(self.redis_port)

    def __init__(
        self, build_path: str, build_ref: str | None, staging_directory: str,
        resource_prefix: str, port_offset: int, cache_backend: str,
        logger: logging.Logger | None=None
    ):
        self.resource_prefix = resource_prefix
        self.port_offset = port_offset

        self._staging_dir = Path(staging_directory)
        self._build_path = Path(build_path).absolute()
        self._build_ref = build_ref
        self._cache_backend = cache_backend

        if logger is None:
            logger = LOGGER
        self.logger = logger
        self._lrr_process = None
        self._lrr_stdout_fh = None
        self._redis_process = None
        self._redis_stdout_fh = None
        self._redis_stderr_fh = None
        self._keg_dir: Path | None = None

    @override
    def apply_plugins(self):
        """
        Write test plugins into the per-module PERL5LIB overlay (not the keg).
        """
        root_dir = self.lrr_plugin_overlay_dir
        for plugin_type, plugin_paths in self.plugin_paths.items():
            if not plugin_paths:
                continue

            target_testing_dir = root_dir / plugin_type / "Testing"
            if target_testing_dir.exists():
                shutil.rmtree(target_testing_dir)
            target_testing_dir.mkdir(parents=True, exist_ok=False)

            for plugin_path in plugin_paths:
                source = Path(plugin_path)
                if not source.exists():
                    raise FileNotFoundError(f"Plugin path does not exist: {source}")
                shutil.copy2(source, target_testing_dir / source.name)

    @override
    def get_lrr_logs(self, tail: int=100) -> bytes:
        if self.lrr_log_path.exists():
            with open(self.lrr_log_path, 'rb') as rb:
                lines = rb.readlines()
                if lines:
                    return b''.join(lines[-tail:])
                self.logger.error(f"No lines found in {self.lrr_log_path}")
        if self.lrr_stdout_path.exists():
            self.logger.error("LRR logs not found; falling back to captured stdout.")
            with open(self.lrr_stdout_path, 'rb') as rb:
                lines = rb.readlines()
                return b''.join(lines[-tail:])
        self.logger.error("No LRR logs are available!")
        return b"No LRR logs available."

    @override
    def get_redis_logs(self, tail: int=100) -> bytes:
        if self.redis_log_path.exists():
            with open(self.redis_log_path, 'rb') as rb:
                lines = rb.readlines()
                if lines:
                    return b''.join(lines[-tail:])
                self.logger.error(f"No lines found in {self.redis_log_path}")
        self.logger.error("No Redis logs are available!")
        return b"No Redis logs available."

    @override
    def read_redis_logs(self) -> str:
        if self.redis_log_path.exists():
            with open(self.redis_log_path, encoding='utf-8', errors='replace') as f:
                return f.read()
        self.logger.warning("No Redis logs are available!")
        return ""

    @override
    def setup(
        self, with_api_key: bool=False, with_nofunmode: bool=False, enable_cors: bool=False, lrr_debug_mode: bool=False,
        environment: dict[str, str]={}, plugin_paths: PluginPathsT={},
        test_connection_max_retries: int=4
    ):
        """
        Set up the LANraragi environment: ensure the keg is installed, then start redis and
        LRR, injecting Redis configuration between startups to avoid an LRR restart.

        Args:
            with_api_key: whether to add a default API key to LRR
            with_nofunmode: whether to start LRR with nofunmode on
            enable_cors: whether to enable/disable CORS during startup
            lrr_debug_mode: whether to start LRR with debug mode on
            environment: additional environment variables map to pass through to the LRR process
            test_connection_max_retries: connection retries for server readiness
        """
        self._setup_environment = dict(environment)
        lrr_port = self.lrr_port
        redis_port = self.redis_port

        staging_dir = self.staging_dir
        if not staging_dir.exists():
            raise FileNotFoundError(f"Staging directory {staging_dir} not found.")
        if not self.build_path.exists():
            raise FileNotFoundError(f"LRR build path {self.build_path} not found.")

        self._ensure_keg()

        self.plugin_paths = plugin_paths
        self.apply_plugins()

        self.logger.info(
            f"Deploying Homebrew LRR with the following resources: LRR port {lrr_port}, "
            f"Redis port {redis_port}, content path {self.archives_dir}, keg {self.keg_dir}."
        )

        for directory in (self.archives_dir, self.thumb_dir, self.temp_dir, self.logs_dir, self.redis_dir):
            if directory.exists():
                self.logger.debug(f"Directory exists: {directory}")
            else:
                self.logger.debug(f"Creating directory: {directory}")
                directory.mkdir(parents=True, exist_ok=False)

        if _is_port_free(redis_port):
            self.start_redis()
            self.test_redis_connection()
            self.logger.debug(f"Redis service is established on port {redis_port}.")
        else:
            self.test_redis_connection()
            self.logger.debug(f"Running Redis service confirmed on port {redis_port}, skipping startup.")
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
        self.disable_openapi_bypass()
        self.logger.debug("Redis post-connect configuration complete.")

        if _is_port_free(lrr_port):
            self.start_lrr()
            self.test_lrr_connection(lrr_port, test_connection_max_retries)
            self.logger.debug(f"LRR service is established on port {lrr_port}.")
        else:
            self.logger.debug(f"Found running LRR service on port {lrr_port}. Restarting...")
            self.stop_lrr()
            self.start_lrr()
            self.test_lrr_connection(lrr_port, test_connection_max_retries)
            self.logger.debug("LRR service restarted.")

        self.logger.info(f"Completed setup of LANraragi. LRR PID = {self.lrr_pid}; Redis PID = {self.redis_pid}.")

    @override
    def start(self, test_connection_max_retries: int=4):
        """
        Start LRR and Redis, skipping startup for services already running.
        """
        redis_port = self.redis_port
        if _is_port_free(redis_port):
            self.start_redis()
            self.test_redis_connection()
            self.logger.debug(f"Redis service is established on port {redis_port}.")
        else:
            self.test_redis_connection()
            self.logger.debug(f"Running Redis service confirmed on port {redis_port}, skipping startup.")

        lrr_port = self.lrr_port
        if _is_port_free(lrr_port):
            self.start_lrr()
            self.test_lrr_connection(lrr_port, test_connection_max_retries)
            self.logger.debug(f"LRR service established on port {lrr_port}.")
        else:
            self.test_lrr_connection(lrr_port, test_connection_max_retries)
            self.logger.debug(f"Running LRR service confirmed on port {lrr_port}, skipping startup.")

    @override
    def stop(self):
        self.stop_lrr()
        self.logger.debug("Stopped LRR.")
        self.stop_redis()
        self.logger.debug("Stopped Redis.")

    @override
    def restart(self):
        self.stop()
        self.start()

    @override
    def teardown(self, remove_data: bool=False):
        """
        Stop LRR and Redis and remove per-module data. The keg is left installed
        (session-shared build artifact).
        """
        self.stop()
        if hasattr(self, "_redis_client") and self._redis_client is not None:
            self._redis_client.close()
        if remove_data:
            for directory in (self.archives_dir, self.thumb_dir, self.temp_dir, self.logs_dir, self.redis_dir, self.overlay_dir):
                if directory.exists():
                    shutil.rmtree(directory)
                    self.logger.debug(f"Removed directory: {directory}")

    @override
    def start_lrr(self):
        """
        Start the LRR server from the keg; new session enables process-group signalling on stop.
        """
        cwd = os.getcwd()
        try:
            # lrr.conf is resolved relative to cwd at app startup.
            os.chdir(self.keg_libexec)

            for directory in (self.logs_dir, self.temp_dir, self.thumb_dir):
                if not directory.exists():
                    self.logger.debug(f"Creating directory: {directory}")
                    directory.mkdir(parents=True, exist_ok=False)

            lrr_env = self._lrr_env()
            script = [
                str(self.perl_exe_path), str(self.lrr_launcherpl_path),
                "-d", str(self.lrr_lanraragi_path),
            ]
            self.logger.info(
                f"(lrr_network={self.lrr_base_url}, lrr_data_directory={self.archives_dir}, "
                f"lrr_log_directory={self.logs_dir}) running script {subprocess.list2cmdline(script)}"
            )

            self._close_fh("_lrr_stdout_fh")
            self._lrr_stdout_fh = open(self.lrr_stdout_path, "w")  # noqa: SIM115
            self._lrr_process = subprocess.Popen(
                script,
                env=lrr_env,
                stdout=self._lrr_stdout_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            self.logger.debug(f"Started LRR process with PID: {self._lrr_process.pid}.")
        finally:
            os.chdir(cwd)

    @override
    def start_redis(self):
        """
        Start the DB server (redis/valkey per --cache-backend) from the keg's conf.
        """
        logs_dir = self.logs_dir
        redis_dir = self.redis_dir
        if not logs_dir.exists():
            self.logger.debug(f"Creating logs directory: {logs_dir}")
            logs_dir.mkdir(parents=True, exist_ok=False)
        if not redis_dir.exists():
            self.logger.debug(f"Creating redis directory: {redis_dir}")
            redis_dir.mkdir(parents=True, exist_ok=False)

        script = [
            str(self.db_server_exe), str(self.keg_redis_conf),
            # The keg's redis.conf ships "daemonize yes" (for the bin/lanraragi wrapper); force
            # foreground so the server stays our child and can be signalled/stopped.
            "--daemonize", "no",
            "--dir", str(redis_dir),
            "--logfile", str(self.redis_log_path),
            "--port", str(self.redis_port),
        ]
        self.logger.debug(f"(redis_dir={redis_dir}, redis_logfile_path={self.redis_log_path}) running script {subprocess.list2cmdline(script)}")

        # Capture stdout/stderr to files for errors emitted before the logfile opens.
        self._close_fh("_redis_stdout_fh")
        self._close_fh("_redis_stderr_fh")
        self._redis_stdout_fh = open(self.logs_dir / "redis-stdout.log", "w")  # noqa: SIM115
        self._redis_stderr_fh = open(self.logs_dir / "redis-stderr.log", "w")  # noqa: SIM115
        self._redis_process = subprocess.Popen(
            script,
            stdout=self._redis_stdout_fh,
            stderr=self._redis_stderr_fh,
            start_new_session=True,
        )
        self.logger.debug(f"Started redis service with PID {self._redis_process.pid}.")

    @override
    def stop_lrr(self, timeout: int=10):
        """
        Stop the LRR server by signalling its process group, falling back to the port owner.
        """
        port = self.lrr_port
        if _is_port_free(port):
            self.logger.debug(f"Confirmed port availability on port: {port}")
            self._lrr_process = None
            self._close_fh("_lrr_stdout_fh")
            return

        proc = self._lrr_process
        if proc is not None and proc.poll() is None:
            self._terminate_process_group(proc.pid, timeout)
        elif pid := self.lrr_pid:
            self._terminate_process_group(pid, timeout)

        self._lrr_process = None
        self._close_fh("_lrr_stdout_fh")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if _is_port_free(port):
                self.logger.debug(f"Confirmed LRR port availability: {port}")
                return
            time.sleep(0.5)
        raise DeploymentException(f"Failed to stop LRR and free port {port} within {timeout}s!")

    @override
    def stop_redis(self, timeout: int=10):
        """
        Stop the DB server by signalling its process group, falling back to the port owner.
        """
        port = self.redis_port
        proc = self._redis_process
        if proc is not None and proc.poll() is None:
            self._terminate_process_group(proc.pid, timeout)
        elif pid := self.redis_pid:
            self._terminate_process_group(pid, timeout)
        self._redis_process = None
        self._close_fh("_redis_stdout_fh")
        self._close_fh("_redis_stderr_fh")

        deadline = time.time() + timeout
        while time.time() < deadline:
            if _is_port_free(port):
                self.logger.debug(f"Redis port {port} is available.")
                return
            time.sleep(0.5)
        raise DeploymentException(f"Failed to stop Redis and free port {port} within {timeout}s!")

    def _ensure_keg(self):
        version = self._keg_version()
        if self._keg_installed(version):
            self.logger.info(f"Reusing installed lanraragi keg {version}.")
        else:
            self.logger.info(f"Building lanraragi keg {version} from {self.build_path}.")
            self._build_keg(version)
        self._keg_dir = self._resolve_keg_dir()
        self.logger.debug(f"Resolved keg directory: {self._keg_dir}")

    def _keg_installed(self, version: str) -> bool:
        return bool(list(self._cellar_keg_root().glob(f"{version}*")))

    def _build_keg(self, version: str):
        formula_src = self.build_path / "tools" / "build" / "homebrew" / "Lanraragi.rb"
        if not formula_src.exists():
            raise DeploymentException(f"Homebrew formula not found in LRR source: {formula_src}")

        # Homebrew rejects installing from a bare formula path; the formula must live in a tap.
        tap = "lrrtest/lanraragi"
        tap_dir = self._brew_repository() / "Library" / "Taps" / "lrrtest" / "homebrew-lanraragi"
        if not tap_dir.exists():
            # tap-new makes an initial git commit; supply an identity in case the host has none.
            env = os.environ.copy()
            env.setdefault("GIT_AUTHOR_NAME", "lrrtest")
            env.setdefault("GIT_AUTHOR_EMAIL", "lrrtest@localhost")
            env.setdefault("GIT_COMMITTER_NAME", "lrrtest")
            env.setdefault("GIT_COMMITTER_EMAIL", "lrrtest@localhost")
            output = subprocess.run(
                ["brew", "tap-new", tap],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
            )
            if output.returncode != 0:
                raise DeploymentException(f"brew tap-new failed for {tap} (rc={output.returncode}): {output.stderr.strip()}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tarball = Path(tmpdir) / "lanraragi-source.tar.gz"
            self._make_source_tarball(tarball)
            sha256 = _sha256_file(tarball)

            formula = self._render_formula(formula_src.read_text(), tarball, sha256, version)
            formula_dst = tap_dir / "Formula" / "lanraragi.rb"
            formula_dst.parent.mkdir(parents=True, exist_ok=True)
            formula_dst.write_text(formula)

            # brew install output is left uncaptured so the long build streams to the log.
            output = subprocess.run(
                ["brew", "install", "--build-from-source", f"{tap}/lanraragi"],
                stdin=subprocess.DEVNULL,
            )
            if output.returncode != 0:
                raise DeploymentException(f"brew install failed for {tap}/lanraragi (rc={output.returncode}).")

        # The formula points at the now-deleted source tarball; remove it so the persistent tap
        # keeps no dead file:// url. The installed keg is resolved by Cellar path, not the formula.
        formula_dst.unlink(missing_ok=True)

    def _make_source_tarball(self, dest: Path):
        """
        Produce a tar.gz of the LRR source under test (the working tree, or build_ref if set).
        """
        tree = self._build_ref
        if not tree:
            stash = subprocess.run(
                ["git", "-C", str(self.build_path), "stash", "create"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
            ).stdout.strip()
            tree = stash or "HEAD"
        output = subprocess.run(
            ["git", "-C", str(self.build_path), "archive", "--format=tar.gz", "-o", str(dest), tree],
            stdin=subprocess.DEVNULL,
        )
        if output.returncode != 0:
            raise DeploymentException(f"git archive failed for ref {tree} in {self.build_path} (rc={output.returncode}).")

    def _content_key(self) -> str:
        """Short hash of the source state: HEAD + working-tree diff + status (covers the formula)."""
        if self._build_ref:
            state = subprocess.run(
                ["git", "-C", str(self.build_path), "rev-parse", self._build_ref],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
            ).stdout.strip()
        else:
            head = subprocess.run(
                ["git", "-C", str(self.build_path), "rev-parse", "HEAD"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
            ).stdout.strip()
            porcelain = subprocess.run(
                ["git", "-C", str(self.build_path), "status", "--porcelain"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
            ).stdout
            diff = subprocess.run(
                ["git", "-C", str(self.build_path), "diff", "HEAD"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
            ).stdout
            state = head + porcelain + diff
        return hashlib.sha1(state.encode("utf-8")).hexdigest()[:12]

    def _keg_version(self) -> str:
        if not hasattr(self, "_keg_version_cache"):
            self._keg_version_cache = f"{self._formula_base_version()}-{self._content_key()}"
        return self._keg_version_cache

    def _formula_base_version(self) -> str:
        formula_src = self.build_path / "tools" / "build" / "homebrew" / "Lanraragi.rb"
        match = re.search(r'^\s*version\s+"([^"]*)"', formula_src.read_text(), re.MULTILINE)
        if not match:
            raise DeploymentException(f"Could not read 'version' from formula {formula_src}.")
        return match.group(1)

    def _render_formula(self, formula: str, tarball: Path, sha256: str, version: str) -> str:
        """
        Rewrite the formula header (url/version/sha256) for a local tarball build; the
        resource/install body is left untouched.
        """
        body_match = re.search(r'^\s*(resource\b|def\s+install\b)', formula, re.MULTILINE)
        split_at = body_match.start() if body_match else len(formula)
        header, body = formula[:split_at], formula[split_at:]

        # Drop head/revision/any standalone sha256 so the file:// build is clean and the
        # keg version equals our content hash (no revision _N suffix on the Cellar dir).
        header = re.sub(r'^\s*head\s+.*\n', '', header, flags=re.MULTILINE)
        header = re.sub(r'^\s*revision\s+\d+\s*\n', '', header, flags=re.MULTILINE)
        header = re.sub(r'^\s*sha256\s+"[^"]*"\s*\n', '', header, flags=re.MULTILINE)

        url_stanza = re.compile(r'^\s*url\s+"[^"]*"(\s*,\s*\n\s*revision:\s*"[^"]*")?', re.MULTILINE)
        new_url = f'  url "file://{tarball}"\n  sha256 "{sha256}"'
        header, url_subs = url_stanza.subn(new_url, header, count=1)
        if url_subs != 1:
            raise DeploymentException("Could not rewrite the formula 'url' stanza; formula shape unexpected.")

        version_line = re.compile(r'^(\s*version\s+)"[^"]*"', re.MULTILINE)
        header, ver_subs = version_line.subn(rf'\g<1>"{version}"', header, count=1)
        if ver_subs != 1:
            raise DeploymentException("Could not rewrite the formula 'version'; formula shape unexpected.")

        return header + body

    def _resolve_keg_dir(self) -> Path:
        # Resolve our exact keg by Cellar path, not the linked keg ('brew --prefix
        # lanraragi'), so coexisting branch kegs cannot shadow each other.
        matches = sorted(self._cellar_keg_root().glob(f"{self._keg_version()}*"))
        if not matches:
            raise DeploymentException(f"No installed keg for version {self._keg_version()} under {self._cellar_keg_root()}.")
        return matches[0]

    def _cellar_keg_root(self) -> Path:
        output = subprocess.run(
            ["brew", "--cellar"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if output.returncode != 0:
            raise DeploymentException(f"Could not resolve Homebrew Cellar (rc={output.returncode}): {output.stderr.strip()}")
        return Path(output.stdout.strip()) / "lanraragi"

    def _brew_prefix(self, formula: str) -> Path:
        output = subprocess.run(
            ["brew", "--prefix", formula],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if output.returncode != 0:
            raise DeploymentException(f"Could not resolve brew prefix for {formula} (rc={output.returncode}): {output.stderr.strip()}")
        return Path(output.stdout.strip())

    def _brew_repository(self) -> Path:
        output = subprocess.run(
            ["brew", "--repository"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if output.returncode != 0:
            raise DeploymentException(f"Could not resolve Homebrew repository (rc={output.returncode}): {output.stderr.strip()}")
        return Path(output.stdout.strip())

    def _lrr_env(self) -> dict[str, str]:
        lrr_env = os.environ.copy()
        overlay = str(self.overlay_dir)
        existing_perl5lib = lrr_env.get("PERL5LIB", "")
        perl5lib = overlay + os.pathsep + str(self.keg_perl5lib)
        if existing_perl5lib:
            perl5lib = perl5lib + os.pathsep + existing_perl5lib
        lrr_env["PERL5LIB"] = perl5lib
        lrr_env["LRR_NETWORK"] = self.lrr_base_url
        lrr_env["LRR_DATA_DIRECTORY"] = str(self.archives_dir)
        lrr_env["LRR_LOG_DIRECTORY"] = str(self.logs_dir)
        lrr_env["LRR_TEMP_DIRECTORY"] = str(self.temp_dir)
        lrr_env["LRR_THUMB_DIRECTORY"] = str(self.thumb_dir)
        lrr_env["LRR_REDIS_ADDRESS"] = f"127.0.0.1:{self.redis_port}"
        # Keep the manager attached so we can signal the whole tree on stop.
        lrr_env["HYPNOTOAD_FOREGROUND"] = "1"
        # macOS only: metadata plugins crash on fork without this.
        if sys.platform == "darwin":
            lrr_env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
        if hasattr(self, "_setup_environment") and self._setup_environment:
            lrr_env.update(self._setup_environment)
        return lrr_env

    def _terminate_process_group(self, pid: int, timeout: int):
        """
        Signal a process group with SIGTERM, escalating to SIGKILL if it does not exit.
        """
        # Both errors mean the group is gone: macOS raises PermissionError for a zombie
        # leader (exited, not yet reaped) where Linux raises ProcessLookupError.
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.2)
        self.logger.warning(f"Process group {pgid} did not exit after SIGTERM; sending SIGKILL.")
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)

    def _close_fh(self, attr: str):
        fh = getattr(self, attr, None)
        if fh is not None:
            with contextlib.suppress(OSError):
                fh.close()
            setattr(self, attr, None)

def _is_port_free(port: int) -> bool:
    """
    Whether 127.0.0.1:port is bindable. SO_REUSEADDR lets a TIME_WAIT port (post graceful
    shutdown) pass, matching how redis/LRR bind; it still refuses a live listener.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

def _get_port_owner_pid(port: int) -> int | None:
    try:
        output = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
    except OSError:
        # lsof may not be installed on Linux hosts; treat as unknown PID.
        return None
    pid = output.stdout.strip().splitlines()
    return int(pid[0]) if pid and pid[0].isdigit() else None

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            digest.update(chunk)
    return digest.hexdigest()
