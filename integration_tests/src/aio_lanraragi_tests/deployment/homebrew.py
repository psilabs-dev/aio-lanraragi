"""Run LANraragi and Redis from a Homebrew build shared by a pytest session."""

import contextlib
import hashlib
import logging
import os
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import NamedTuple, override
from uuid import uuid4

import psutil
from redis.exceptions import ConnectionError as RedisConnectionError

from aio_lanraragi_tests.common import DEFAULT_API_KEY, is_port_available
from aio_lanraragi_tests.deployment.base import (
    AbstractLRRDeploymentContext,
    PluginPathsT,
)
from aio_lanraragi_tests.exceptions import DeploymentException

LOGGER = logging.getLogger(__name__)
_FORMULA_PATH = "tools/build/homebrew/Lanraragi.rb"


class HomebrewBuild(NamedTuple):
    libexec: Path
    perl: Path
    redis: Path


def _run(command: list[str], *, timeout: int = 60, stream: bool = False) -> str:
    try:
        result = subprocess.run(
            command, check=True, timeout=timeout, stdin=subprocess.DEVNULL,
            capture_output=not stream, text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise DeploymentException(
            f"{subprocess.list2cmdline(command)} exited with status {exc.returncode}. {detail}"
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeploymentException(f"Could not run {subprocess.list2cmdline(command)}: {exc}") from exc
    return result.stdout.strip() if result.stdout is not None else ""


def install_homebrew_build(source: Path, ref: str | None, cleanup: contextlib.ExitStack) -> HomebrewBuild:
    """Install one uniquely named keg in a tap owned by this test session."""
    suffix = uuid4().hex
    tap = f"lrrtest/session-{suffix}"
    formula_name = f"lanraragi-test-run{suffix}"
    formula_ref = f"{tap}/{formula_name}"
    brew_repo = Path(_run(["brew", "--repository"]))
    cellar = Path(_run(["brew", "--cellar"])) / formula_name
    tap_dir = brew_repo / "Library" / "Taps" / "lrrtest" / f"homebrew-session-{suffix}"
    if tap_dir.exists() or cellar.exists():
        raise DeploymentException(f"Homebrew session name is already in use: {tap}.")

    with tempfile.TemporaryDirectory(prefix="lrr-brew-") as directory:
        archive = Path(directory) / "lanraragi.tar.gz"
        git = ["git", "-C", str(source)]
        if ref is not None:
            commit = _run([*git, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"])
            _run([*git, "archive", "--format=tar.gz", "-o", str(archive), commit], timeout=600)
        else:
            try:
                files = subprocess.run(
                    [*git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                    check=True, capture_output=True, timeout=60,
                ).stdout
            except subprocess.CalledProcessError as exc:
                detail = exc.stderr.decode("utf-8", errors="replace").strip()
                raise DeploymentException(f"Could not list source files in {source}: {detail}") from exc
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise DeploymentException(f"Could not list source files in {source}: {exc}") from exc
            with tarfile.open(archive, "w:gz") as tar:
                for filename in dict.fromkeys(files.split(b"\0")):
                    if filename:
                        relative = os.fsdecode(filename)
                        path = source / relative
                        if path.is_file() or path.is_symlink():
                            tar.add(path, arcname=relative, recursive=False)
        with tarfile.open(archive) as tar:
            try:
                member = tar.getmember(_FORMULA_PATH)
            except KeyError as exc:
                raise DeploymentException(f"Homebrew formula missing from {ref or source}: {_FORMULA_PATH}") from exc
            if not member.isfile():
                raise DeploymentException(f"Expected a regular Homebrew formula at {_FORMULA_PATH}.")
            with tar.extractfile(member) as formula_file:
                formula = formula_file.read().decode("utf-8")

        with archive.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()

        # Rewrite only the source and package identity of the bundled formula.
        formula, class_count = re.subn(
            r"^class Lanraragi < Formula[ \t]*$",
            f'class LanraragiTestRun{suffix} < Formula\n  keg_only "temporary integration test build"',
            formula, count=1, flags=re.MULTILINE,
        )
        formula, url_count = re.subn(
            r'^  url "[^"\n]+"(?:,\n[ \t]+revision: "[^"\n]+")?[ \t]*\n(?:  sha256 "[^"\n]+"[ \t]*\n)?',
            f'  url "{archive.as_uri()}"\n  sha256 "{checksum}"\n',
            formula, count=1, flags=re.MULTILINE,
        )
        if (class_count, url_count) != (1, 1):
            raise DeploymentException(f"Unexpected Homebrew formula header in {_FORMULA_PATH}.")

        try:
            _run(["brew", "tap-new", "--no-git", tap], timeout=300)
        finally:
            if tap_dir.is_dir():
                cleanup.callback(_run, ["brew", "untap", tap], timeout=300)
        (tap_dir / "Formula" / f"{formula_name}.rb").write_text(formula, encoding="utf-8")
        try:
            _run(["brew", "install", "--build-from-source", formula_ref], timeout=3600, stream=True)
        finally:
            if cellar.is_dir() and any(cellar.iterdir()):
                cleanup.callback(_run, ["brew", "uninstall", "--force", formula_ref], timeout=300)

    prefix = Path(_run(["brew", "--prefix", formula_ref]))
    return HomebrewBuild(
        prefix / "libexec",
        Path(_run(["brew", "--prefix", "perl"])) / "bin" / "perl",
        Path(_run(["brew", "--prefix", "redis"])) / "bin" / "redis-server",
    )


class HomebrewLRRDeploymentContext(AbstractLRRDeploymentContext):

    @property
    @override
    def staging_dir(self) -> Path:
        return self._staging_dir

    @property
    @override
    def archives_dir(self) -> Path:
        return self.staging_dir / f"{self.resource_prefix}archives"

    @property
    @override
    def logs_dir(self) -> Path:
        return self.staging_dir / f"{self.resource_prefix}log"

    @property
    def temp_dir(self) -> Path:
        # LRR reads worker pidfiles at startup, including after an interrupted test session.
        return self.staging_dir / f"{self.resource_prefix}temp-{self._process_token}"

    @property
    def thumb_dir(self) -> Path:
        return self.staging_dir / f"{self.resource_prefix}thumb"

    @property
    def plugin_dir(self) -> Path:
        return self.staging_dir / f"{self.resource_prefix}plugins"

    def __init__(
        self, build: HomebrewBuild, staging: Path, resource_prefix: str, port_offset: int,
        logger: logging.Logger | None = None,
    ) -> None:
        self._build = build
        self._staging_dir = staging
        self.resource_prefix = resource_prefix
        self.port_offset = port_offset
        self.logger = logger if logger is not None else LOGGER
        self._lrr_process: subprocess.Popen[bytes] | None = None
        self._redis_process: subprocess.Popen[bytes] | None = None
        self._environment: dict[str, str] = {}
        self._process_token = uuid4().hex

    @override
    def apply_plugins(self) -> None:
        for plugin_type, paths in self.plugin_paths.items():
            for path in paths:
                source = Path(path)
                target = self.plugin_dir / "LANraragi" / "Plugin" / plugin_type / "Testing" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    @override
    def get_lrr_logs(self, tail: int = 100) -> bytes:
        logs = b""
        for path in (self.lanraragi_logs_path, self.logs_dir / "lanraragi-stdout.log"):
            if path.is_file():
                with path.open("rb") as stream:
                    logs += b"".join(stream.readlines()[-tail:])
        return logs

    @override
    def get_redis_logs(self, tail: int = 100) -> bytes:
        return b"".join(self._redis_log().splitlines(keepends=True)[-tail:])

    @override
    def read_redis_logs(self) -> str:
        return self._redis_log().decode("utf-8", errors="replace")

    @override
    def setup(
        self, with_api_key: bool = False, with_nofunmode: bool = False, enable_cors: bool = False,
        lrr_debug_mode: bool = False, environment: dict[str, str] | None = None,
        plugin_paths: PluginPathsT | None = None, test_connection_max_retries: int = 4,
    ) -> None:
        self._environment = dict(environment or {})
        self.plugin_paths = plugin_paths or {}
        self.apply_plugins()
        for path in (self.archives_dir, self.logs_dir, self.temp_dir, self.thumb_dir, self.redis_dir):
            path.mkdir(exist_ok=True)

        self._start_and_wait_for_redis()
        self.update_api_key(DEFAULT_API_KEY if with_api_key else None)
        if with_nofunmode:
            self.enable_nofun_mode()
        if lrr_debug_mode:
            self.enable_lrr_debug_mode()
        if enable_cors:
            self.enable_cors()
        else:
            self.disable_cors()
        self.disable_openapi_bypass()
        self._start_and_wait_for_lrr(test_connection_max_retries)

    @override
    def start(self, test_connection_max_retries: int = 4) -> None:
        self._start_and_wait_for_redis()
        self._start_and_wait_for_lrr(test_connection_max_retries)

    @override
    def stop(self) -> None:
        try:
            self.stop_lrr()
        finally:
            self.stop_redis()

    @override
    def restart(self) -> None:
        self.stop()
        self.start()

    @override
    def teardown(self, remove_data: bool = False) -> None:
        self.stop()
        redis_client = getattr(self, "_redis_client", None)
        if redis_client is not None:
            redis_client.close()
        if remove_data:
            for path in (
                self.archives_dir, self.logs_dir, self.temp_dir, self.thumb_dir, self.redis_dir, self.plugin_dir,
            ):
                if path.exists():
                    shutil.rmtree(path)

    @override
    def start_lrr(self) -> None:
        env = os.environ | self._environment
        perl_paths = [str(self.plugin_dir), str(self._build.libexec / "lib" / "perl5")]
        if env.get("PERL5LIB"):
            perl_paths.append(env["PERL5LIB"])
        env.update(
            PERL5LIB=os.pathsep.join(perl_paths),
            LRR_NETWORK=self.lrr_base_url,
            LRR_DATA_DIRECTORY=str(self.archives_dir),
            LRR_LOG_DIRECTORY=str(self.logs_dir),
            LRR_TEMP_DIRECTORY=str(self.temp_dir),
            LRR_THUMB_DIRECTORY=str(self.thumb_dir),
            LRR_REDIS_ADDRESS=f"127.0.0.1:{self.redis_port}",
            LRR_AIO_TEST_PROCESS_TOKEN=self._process_token,
        )
        if os.uname().sysname == "Darwin":
            env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
        workspace = self._build.libexec
        self._lrr_process = self._start_process(
            "LANraragi", self.lrr_port, self.logs_dir / "lanraragi-stdout.log", self._lrr_process,
            [str(self._build.perl), "script/launcher.pl", "-f", "script/lanraragi"], env=env, cwd=workspace,
        )

    @override
    def start_redis(self) -> None:
        self._redis_process = self._start_process(
            "Redis", self.redis_port, self.logs_dir / "redis.log", self._redis_process,
            [
                str(self._build.redis), str(self._build.libexec / "redis.conf"),
                "--daemonize", "no", "--bind", "127.0.0.1", "--dir", str(self.redis_dir),
                "--pidfile", str(self.redis_dir / "redis.pid"), "--logfile", "", "--port", str(self.redis_port),
            ],
        )

    @override
    def stop_lrr(self, timeout: int = 10) -> None:
        if self._lrr_process is None:
            return
        try:
            self._stop_groups({self._lrr_process.pid}, timeout, self._lrr_process)
        finally:
            self._stop_lrr_workers(timeout)
        self._lrr_process = None

    @override
    def stop_redis(self, timeout: int = 10) -> None:
        if self._redis_process is not None:
            self._stop_groups({self._redis_process.pid}, timeout, self._redis_process)
            self._redis_process = None

    def _start_and_wait_for_redis(self) -> None:
        self.start_redis()
        try:
            self.test_redis_connection()
        except RedisConnectionError:
            status = self._redis_process.poll() if self._redis_process is not None else None
            logs = self.get_redis_logs(tail=30).decode("utf-8", errors="replace")
            self.logger.exception(f"Redis did not become ready on port {self.redis_port} (exit status: {status}).\n{logs}")
            raise

    def _start_and_wait_for_lrr(self, retries: int) -> None:
        self.start_lrr()
        try:
            self.test_lrr_connection(self.lrr_port, retries)
        except DeploymentException:
            status = self._lrr_process.poll() if self._lrr_process is not None else None
            logs = self.get_lrr_logs(tail=30).decode("utf-8", errors="replace")
            self.logger.exception(f"LANraragi did not become ready on port {self.lrr_port} (exit status: {status}).\n{logs}")
            raise

    def _redis_log(self) -> bytes:
        path = self.logs_dir / "redis.log"
        return path.read_bytes() if path.is_file() else b""

    def _start_process(
        self, name: str, port: int, log: Path, process: subprocess.Popen[bytes] | None,
        command: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None,
    ) -> subprocess.Popen[bytes]:
        if process is not None:
            raise DeploymentException(f"Stop the previous {name} process before starting it.")
        if not is_port_available(port, reuse_address=True):
            raise DeploymentException(f"{name} port {port} is already in use.")
        self.logger.info(f"Starting {name} on port {port}. Output: {log}")
        with log.open("ab") as stream:
            return subprocess.Popen(
                command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
            )

    def _stop_groups(
        self, groups: set[int], timeout: int, process: subprocess.Popen[bytes] | None = None,
    ) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if process is not None:
                process.poll()
            for pgid in groups.copy():
                try:
                    os.killpg(pgid, sig)
                except ProcessLookupError:
                    groups.remove(pgid)
                except PermissionError:
                    if self._group_has_live_process(pgid):
                        raise
                    groups.remove(pgid)
            deadline = time.monotonic() + timeout
            while groups:
                if process is not None:
                    process.poll()
                for pgid in groups.copy():
                    try:
                        os.killpg(pgid, 0)
                    except ProcessLookupError:
                        groups.remove(pgid)
                    except PermissionError:
                        if self._group_has_live_process(pgid):
                            raise
                        groups.remove(pgid)
                if not groups or time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            if not groups:
                break
        if process is not None:
            process.wait(timeout=timeout)
        live_groups = [pgid for pgid in groups if self._group_has_live_process(pgid)]
        if live_groups:
            raise DeploymentException(f"Process groups did not stop: {sorted(live_groups)}.")

    def _group_has_live_process(self, pgid: int) -> bool:
        # Darwin can return EPERM for a group whose remaining members are zombies.
        for process in psutil.process_iter(["status"]):
            with contextlib.suppress(ProcessLookupError):
                if process.info["status"] != psutil.STATUS_ZOMBIE and os.getpgid(process.pid) == pgid:
                    return True
        return False

    def _stop_lrr_workers(self, timeout: int) -> None:
        # Proc::Simple starts the Minion and Shinobu workers in their own sessions.
        groups: set[int] = set()
        for name in ("minion", "shinobu"):
            path = self.temp_dir / f"{name}.pid-s6"
            if not path.is_file():
                continue
            try:
                worker = psutil.Process(int(path.read_text()))
                if (
                    worker.uids().real == os.getuid()
                    and worker.environ().get("LRR_AIO_TEST_PROCESS_TOKEN") == self._process_token
                    and os.getpgid(worker.pid) == worker.pid
                ):
                    groups.add(worker.pid)
            except (ValueError, psutil.NoSuchProcess, ProcessLookupError):
                pass
        self._stop_groups(groups, timeout)
        for name in ("minion", "shinobu"):
            (self.temp_dir / f"{name}.pid").unlink(missing_ok=True)
            (self.temp_dir / f"{name}.pid-s6").unlink(missing_ok=True)
