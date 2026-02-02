import asyncio
import logging
from typing import AsyncGenerator, Dict, Generator

import numpy as np
import pytest
import pytest_asyncio

from lanraragi.clients.client import LRRClient

from aio_lanraragi_tests.deployment.factory import generate_deployment
from aio_lanraragi_tests.deployment.base import AbstractLRRDeploymentContext
from aio_lanraragi_tests.utils.concurrency import get_bounded_sem

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def resource_prefix() -> Generator[str, None, None]:
    yield "test_"


@pytest.fixture
def port_offset() -> Generator[int, None, None]:
    yield 10

@pytest.fixture
def environment(request: pytest.FixtureRequest, port_offset: int, resource_prefix: str):
    is_lrr_debug_mode: bool = request.config.getoption("--lrr-debug")
    environment: AbstractLRRDeploymentContext = generate_deployment(request, resource_prefix, port_offset, logger=LOGGER)
    environment.setup(with_api_key=True, with_nofunmode=False, lrr_debug_mode=is_lrr_debug_mode)

    # configure environments to session
    environments: Dict[str, AbstractLRRDeploymentContext] = {resource_prefix: environment}
    request.session.lrr_environments = environments

    yield environment
    environment.teardown(remove_data=True)


@pytest.fixture
def npgenerator(request: pytest.FixtureRequest) -> Generator[np.random.Generator, None, None]:
    seed: int = int(request.config.getoption("npseed"))
    generator = np.random.default_rng(seed)
    yield generator


@pytest.fixture
def semaphore() -> Generator[asyncio.BoundedSemaphore, None, None]:
    yield get_bounded_sem()


@pytest_asyncio.fixture
async def lrr_client(environment: AbstractLRRDeploymentContext) -> AsyncGenerator[LRRClient, None]:
    """
    Provides a LRRClient for testing with async cleanup.
    """
    client = environment.lrr_client()
    try:
        yield client
    finally:
        await client.close()