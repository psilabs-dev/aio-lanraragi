import aiohttp
import pytest
from lanraragi.clients.api_context import ApiContextManager
from lanraragi.clients.utils import _build_auth_header

@pytest.fixture
def lrr_address() -> str:
    return "http://127.0.0.1:3000"

@pytest.fixture
def lrr_api_key() -> str:
    return "lanraragi"

def test_changed_api_key(lrr_address: str, lrr_api_key: str):
    api_context_manager = ApiContextManager(lrr_address, lrr_api_key)
    assert api_context_manager.headers["Authorization"] == _build_auth_header("lanraragi")
    api_context_manager.update_api_key(None)
    assert "Authorization" not in api_context_manager.headers

@pytest.mark.asyncio
async def test_client_session_ownership(lrr_address: str, lrr_api_key: str):
    """
    Test ownership of sessions that are passed to an ApiContextManager.
    """
    session = aiohttp.ClientSession()
    try:
        async with ApiContextManager(lrr_address, lrr_api_key, client_session=session):
            pass
        assert not session.closed, "Unowned client session is closed by borrowing context!"
    finally:
        if not session.closed:
            await session.close()

@pytest.mark.asyncio
async def test_connector_ownership(lrr_address: str, lrr_api_key: str):
    """
    Test ownership of connectors that are passed to an ApiContextManager.
    """
    connector = aiohttp.TCPConnector()
    try:
        async with ApiContextManager(lrr_address, lrr_api_key, connector=connector):
            pass
        assert not connector.closed, "Unowned connector is closed by borrowing context!"
    finally:
        if not connector.closed:
            await connector.close()

@pytest.mark.asyncio
async def test_client_session_connector_ownership(lrr_address: str, lrr_api_key: str):
    """
    Test ownership where both connector and session are passed to context.
    """
    connector = aiohttp.TCPConnector()
    session = aiohttp.ClientSession()
    try:
        async with ApiContextManager(lrr_address, lrr_api_key, client_session=session, connector=connector):
            pass
        assert not session.closed, "Unowned client session is closed by borrowing context!"
        assert not connector.closed, "Unowned connector is closed by borrowing context!"
    finally:
        if not session.closed:
            await session.close()
        if not connector.closed:
            await connector.close()

@pytest.mark.asyncio
async def test_connector_in_session_with_apicontext(lrr_address: str, lrr_api_key: str):
    """
    Test connector passed into session, which is passed into context.
    Neither resources should be closed by the context.
    """
    connector = aiohttp.TCPConnector()
    session = aiohttp.ClientSession(connector=connector)
    try:
        async with ApiContextManager(lrr_address, lrr_api_key, client_session=session):
            pass
        assert not session.closed, "Unowned client session is closed by borrowing context!"
        assert not connector.closed, "Unowned connector is closed by borrowing context!"
    finally:
        if not session.closed:
            await session.close()
        if not connector.closed:
            await connector.close()
