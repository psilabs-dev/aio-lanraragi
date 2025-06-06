import abc
import asyncio
import base64
import contextlib
import http
import io
import logging
from typing import Any, Dict, Optional, Tuple, Union, override, TypeVar

import aiohttp
import aiohttp.client_exceptions
from yarl import Query

ApiContextManagerLike = TypeVar('ApiContextManagerLike', bound='ApiContextManager')
class ApiContextManager(contextlib.AbstractAsyncContextManager):
    """
    Base API context management layer for an async LANraragi API client. Provides the required utilities and abstractions
    so as to avoid excessive boilerplate on the API implementation level, and enables a single session to be used across
    multiple concurrent API calls.
    """

    def __init__(
            self,
            lrr_host: str, lrr_api_key: str,
            session: Optional[aiohttp.ClientSession]=None, ssl: bool=True, logger: Optional[logging.Logger]=None
    ):
        if not logger:
            logger = logging.getLogger(__name__)
        self.logger = logger
        self.lrr_host = lrr_host
        self.lrr_api_key = lrr_api_key
        self.headers = {"Authorization": build_auth_header(lrr_api_key)}
        self.session = session
        self.ssl = ssl
        self._created_session = False
        self.initialize_api_groups()

    def initialize_api_groups(self):
        return

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session:
            if not self.ssl:
                self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False))
            else:
                self.session = aiohttp.ClientSession()
            self._created_session = True
        return self.session
    
    async def close(self):
        if self.session and self._created_session:
            await self.session.close()
            self.session = None
            self._created_session = False

    def build_url(self, api: str) -> str:
        """
        Builds the LANraragi server URL.

        Examples:
        - `client.build_url("/api/search")`
        - `client.build_url("/api/archives")`
        """
        return f"{self.lrr_host}{api}"

    @override
    async def __aenter__(self: ApiContextManagerLike) -> ApiContextManagerLike:
        await self._get_session()
        return self
    
    @override
    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type:
            self.logger.error(f"Exception occurred: {exc_type.__name__}: {exc_value}")
        await self.close()
        return None

    async def handle_request(
            self, request_type: http.HTTPMethod, url: str, 
            headers: Dict[str, str], params: Query=None, data: Any=None,
            max_retries: int=0
    ) -> Tuple[int, str]:
        """
        A more controlled API call which represents the boilerplate for handling requests on the HTTP layer.
        Because the LANraragi API requires authentication, headers are automatically required.
        Used if you want to get the direct contents of the HTTP response, and not as a structured DTO.

        Supports retry with exponential backoff to handle transient errors. If max_retries is 0, no retry will be attempted.

        Throws:
        - ValueError: when using an unsupported HTTP method (only supports GET, PUT, POST, DELETE for now)
        - aiohttp.client_exceptions.ClientConnectionError
        - aiohttp.client_exceptions.ClientOSError
        - aiohttp.client_exceptions.ClientConnectorError
        """
        self.logger.debug(f"[{request_type.name}][{url}]")
        retry_count = 0
        while True:
            try:
                match request_type:
                    case http.HTTPMethod.GET:
                        async with (await self._get_session()).get(url=url, headers=headers, params=params, data=data) as async_response:
                            if data:
                                self.logger.warning("GET requests should not include a data field.")
                            return (async_response.status, await async_response.text())
                    case http.HTTPMethod.PUT:
                        if params:
                            self.logger.warning("PUT requests should not include query parameters.")
                        async with (await self._get_session()).put(url=url, headers=headers, params=params, data=data) as async_response:
                            return (async_response.status, await async_response.text())
                    case http.HTTPMethod.POST:
                        if params:
                            self.logger.warning("POST requests should not include query parameters.")
                        async with (await self._get_session()).post(url=url, headers=headers, params=params, data=data) as async_response:
                            return (async_response.status, await async_response.text())
                    case http.HTTPMethod.DELETE:
                        if params:
                            self.logger.warning("DELETE requests should not include query parameters.")
                        async with (await self._get_session()).delete(url=url, headers=headers, params=params, data=data) as async_response:
                            return (async_response.status, await async_response.text())
                    case _:
                        raise ValueError(f"Unsupported HTTP method: {request_type}")
            except (aiohttp.client_exceptions.ClientConnectionError, aiohttp.client_exceptions.ClientOSError, aiohttp.client_exceptions.ClientConnectorError) as aiohttp_error:
                if retry_count >= max_retries:
                    raise aiohttp_error
                retry_count += 1
                self.logger.warning(f"[{request_type.name}][{url}] encountered connection error ({aiohttp_error}); retrying in {2 ** retry_count} seconds...")
                await asyncio.sleep(2 ** retry_count)
                continue
    
    async def download_thumbnail(
            self, url: str, headers: Dict[str, str], params: Query=None
    ) -> Tuple[int, Union[bytes, str]]:
        """
        Specific to downloading thumbnails from the LANraragi server. (/api/archives/:id/thumbnail)
        """
        async with (await self._get_session()).get(url=url, headers=headers, params=params) as async_response:
            if async_response.status == 200:
                buffer = io.BytesIO()
                while True:
                    chunk = await async_response.content.read(1024)
                    if not chunk:
                        break
                    buffer.write(chunk)
                buffer.seek(0)
                return (async_response.status, buffer.getvalue())
            elif async_response.status == 202:
                return (async_response.status, await async_response.text())
            return (async_response.status, await async_response.text())

    async def download_file(
            self, url: str, headers: Dict[str, str], params: Query=None
    ) -> Tuple[int, Union[bytes, str]]:
        """
        Specific to downloading files from the LANraragi server.
        """
        async with (await self._get_session()).get(url=url, headers=headers, params=params) as async_response:
            if async_response.status == 200:
                buffer = io.BytesIO()
                while True:
                    chunk = await async_response.content.read(1024)
                    if not chunk:
                        break
                    buffer.write(chunk)
                buffer.seek(0)
                return (async_response.status, buffer.getvalue())
            return (async_response.status, await async_response.text())

def build_auth_header(lrr_api_key: str) -> str:
    bearer = base64.b64encode(lrr_api_key.encode(encoding='utf-8')).decode('utf-8')
    return f"Bearer {bearer}"
