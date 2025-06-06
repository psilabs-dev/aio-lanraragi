import logging
import pytest

from aio_lanraragi_tests.placeholder import foo

logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_placeholder():
    assert foo() == "bar"
