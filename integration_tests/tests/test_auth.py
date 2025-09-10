import asyncio
import logging
from pathlib import Path
import sys
import tempfile
from typing import List, Tuple
import docker
import numpy as np
import pytest
import pytest_asyncio
import aiohttp
from urllib.parse import urlparse, parse_qs

from lanraragi.clients.client import LRRClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.archive import (
    ClearNewArchiveFlagRequest,
    DeleteArchiveRequest,
    DeleteArchiveResponse,
    ExtractArchiveRequest,
    GetArchiveMetadataRequest,
    GetArchiveThumbnailRequest,
    UpdateArchiveThumbnailRequest,
    UpdateReadingProgressionRequest,
    UploadArchiveRequest,
    UploadArchiveResponse,
)
from lanraragi.models.base import (
    LanraragiErrorResponse,
    LanraragiResponse
)
from lanraragi.models.category import (
    AddArchiveToCategoryRequest,
    AddArchiveToCategoryResponse,
    CreateCategoryRequest,
    DeleteCategoryRequest,
    GetCategoryRequest,
    GetCategoryResponse,
    RemoveArchiveFromCategoryRequest,
    UpdateBookmarkLinkRequest,
    UpdateCategoryRequest
)
from lanraragi.models.database import GetDatabaseStatsRequest
from lanraragi.models.minion import (
    GetMinionJobDetailRequest,
    GetMinionJobStatusRequest
)
from lanraragi.models.misc import (
    GetAvailablePluginsRequest,
    GetOpdsCatalogRequest,
    RegenerateThumbnailRequest
)
from lanraragi.models.search import (
    GetRandomArchivesRequest,
    SearchArchiveIndexRequest
)
from lanraragi.models.tankoubon import (
    AddArchiveToTankoubonRequest,
    CreateTankoubonRequest,
    DeleteTankoubonRequest,
    GetTankoubonRequest,
    RemoveArchiveFromTankoubonRequest,
    TankoubonMetadata,
    UpdateTankoubonRequest,
)

from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.deployment.windows import WindowsLRRDeploymentContext
from aio_lanraragi_tests.deployment.docker import DockerLRRDeploymentContext
from aio_lanraragi_tests.common import compute_upload_checksum, DEFAULT_API_KEY
from aio_lanraragi_tests.archive_generation.enums import ArchivalStrategyEnum
from aio_lanraragi_tests.archive_generation.models import (
    CreatePageRequest,
    WriteArchiveRequest,
    WriteArchiveResponse,
)
from aio_lanraragi_tests.archive_generation.archive import write_archives_to_disk
from aio_lanraragi_tests.archive_generation.metadata import create_tag_generators, get_tag_assignments

logger = logging.getLogger(__name__)

@pytest.fixture
def environment(request: pytest.FixtureRequest):

    global_run_id: int = request.config.global_run_id

    # TODO: this should be refactored.
    environment: AbstractLRRDeploymentContext = None

    # check operating system.
    match sys.platform:
        case 'win32':
            runfile_path: str = request.config.getoption("--windows-runfile")
            windows_content_path: str = request.config.getoption("--windows-content-path")
            environment = WindowsLRRDeploymentContext(
                runfile_path, windows_content_path,
            )

        case 'darwin' | 'linux':
            # TODO: we're assuming macos is used as a development environment with docker installed,
            # not a testing environment; for macos github runners, we would be using them
            # to run homebrew integration tests.

            build_path: str = request.config.getoption("--build")
            image: str = request.config.getoption("--image")
            git_url: str = request.config.getoption("--git-url")
            git_branch: str = request.config.getoption("--git-branch")
            use_docker_api: bool = request.config.getoption("--docker-api")
            docker_client = docker.from_env()
            docker_api = docker.APIClient(base_url="unix://var/run/docker.sock") if use_docker_api else None
            environment = DockerLRRDeploymentContext(
                build_path, image, git_url, git_branch, docker_client, docker_api=docker_api,
                global_run_id=global_run_id, is_allow_uploads=True
            )

    environment.setup(with_api_key=True, with_nofunmode=True)
    request.session.lrr_environment = environment # Store environment in pytest session for access in hooks
    yield environment
    environment.teardown(remove_data=True)

@pytest.fixture
def semaphore():
    yield asyncio.BoundedSemaphore(value=8)

@pytest_asyncio.fixture
async def lanraragi():
    """
    Provides a LRRClient for testing with proper async cleanup.
    """
    client = LRRClient(
        lrr_host="http://localhost:3001",
        lrr_api_key=DEFAULT_API_KEY,
        timeout=10
    )
    try:
        yield client
    finally:
        await client.close()
