import asyncio
import contextlib
import http
import io
import logging
from typing import (
    Any,
    Dict,
    Optional,
    Tuple,
    TypeVar,
    Union,
    override,
)

import aiohttp
import aiohttp.client_exceptions
from yarl import Query

from lanraragi.clients.utils import _build_auth_header

_ApiContextManagerLike = TypeVar('_ApiContextManagerLike', bound='ApiContextManager')
class ApiContextManager(contextlib.AbstractAsyncContextManager):
    """
    Base API context management layer for an async LANraragi API client. Provides the required utilities and abstractions
    so as to avoid excessive boilerplate on the API implementation level, and enables a single session to be used across
    multiple concurrent API calls.
    """

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    @logger.setter
    def logger(self, logger: logging.Logger):
        self._logger = logger

    @property
    def headers(self) -> Dict[str, str]:
        """
        LRR request headers. Is either an empty dict or contains authentication.
        """
        if not hasattr(self, "_headers"):
            self._headers = {}
        return self._headers

    @property
    def lrr_host(self) -> str:
        """
        Base URL for LANraragi service.
        TODO: deprecated in favor of `lrr_base_url`.
        https://github.com/psilabs-dev/aio-lanraragi/issues/106
        """
        return self.lrr_base_url

    @lrr_host.setter
    def lrr_host(self, lrr_host: str):
        # TODO: deprecated in favor of `lrr_base_url`.
        # https://github.com/psilabs-dev/aio-lanraragi/issues/106
        self.lrr_base_url = lrr_host

    @property
    def lrr_base_url(self) -> str:
        """
        Base URL for the LANraragi service.

        Examples:
        - `"http://127.0.0.1:3000"`
        - `"https://lanraragi.example"`
        - `"https://website.com/lanraragi"`
        """
        return self._lrr_base_url
    
    @lrr_base_url.setter
    def lrr_base_url(self, lrr_base_url: str):
        self._lrr_base_url = lrr_base_url
    
    @property
    def lrr_api_key(self) -> Optional[str]:
        """
        Unencoded API key for LANraragi
        """
        return self._lrr_api_key

    @lrr_api_key.setter
    def lrr_api_key(self, lrr_api_key: Optional[str]):
        if lrr_api_key is not None:
            self.headers["Authorization"] = _build_auth_header(lrr_api_key)
        else:
            if "Authorization" in self.headers:
                del self.headers["Authorization"]
        self._lrr_api_key = lrr_api_key

    @property
    def owns_client_session(self) -> bool:
        """
        Readonly property set on instantiation, which indicates whether this context
        owns its aiohttp.ClientSession resource.

        If owned, the resource must be closed on context exit.
        """
        return self._owns_client_session
    
    @property
    def owns_connector(self) -> bool:
        """
        Readonly property set on instantiation, which indicates whether this context
        owns its aiohttp.BaseConnector resource.

        If owned, the resource must be closed on context exit.
        """
        return self._owns_connector

    def __init__(
            self,
            lrr_base_url: str, lrr_api_key: Optional[str]=None,
            ssl: bool=True,
            session: Optional[aiohttp.ClientSession]=None,
            client_session: Optional[aiohttp.ClientSession]=None,
            connector: Optional[aiohttp.BaseConnector]=None,
            logger: Optional[logging.Logger]=None
    ):
        """
        Instantiates an ApiContextManager instance and context.
        Any resource not provided by the user will be created and owned by
        the context. On context exit, these resources will be closed.
        """
        if not logger:
            logger = logging.getLogger(__name__)
        self.logger = logger
        self.lrr_base_url = lrr_base_url
        self.lrr_api_key = lrr_api_key

        # aiohttp-specific properties
        # if client session is configured by user, it overrides all other configurations.
        # on context exit, the client (and its attributes) will NOT be cleaned.

        self._session_lock = asyncio.Lock()
        # Prefer explicit client_session; keep deprecated `session` for backward compatibility
        client_session = session if session else client_session # TODO: https://github.com/psilabs-dev/aio-lanraragi/issues/106
        if client_session:
            self.client_session = client_session
            self.connector = None
            self._owns_client_session = False
            self._owns_connector = False
        else:
            self.client_session = None
            self._owns_client_session = True

            # if connector is configured, overrides SSL preference, 
            # and connector will NOT be cleaned on context exit.
            # Otherwise, we will create the connector during session create.
            # A connector will NEVER be created in isolation.
            if connector:
                self.connector = connector
                self._owns_connector = False
            else:
                self.connector = None
                self._owns_connector = True

        # aiohttp-specific convenience configurations
        self.ssl = ssl
        self.initialize_api_groups()

    def initialize_api_groups(self):
        """
        A stub to be overridden by LRRClient to be used at post-construct time,
        as auth data created during construct-time will be passed down to child
        clients.
        """
        return

    def update_api_key(self, api_key: Optional[str]):
        """
        Update the API key.

        If api_key is None, the API key will be removed.
        """
        if api_key is None:
            if "Authorization" in self.headers:
                del self.headers["Authorization"]
        else:
            self.headers["Authorization"] = _build_auth_header(api_key)

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Returns a client session to use.
        If user provided a session during instantiation, this will return that session.
        If user did not provide a session, but instead a connector, this will create an owned session with that connector.
        If user did not provide aiohttp resources, this will create owned resources.

        All owned aiohttp resources will be automatically closed.
        """

        if self.client_session:
            return self.client_session
        
        # only one session can be created and owned by the context manager at any given time.
        async with self._session_lock:
            if self.client_session:
                return self.client_session
            if self.connector:
                self.client_session = aiohttp.ClientSession(connector=self.connector, connector_owner=False)
            elif self.ssl is not None:
                self.client_session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl))
            else:
                self.client_session = aiohttp.ClientSession()
        return self.client_session

    async def close(self):
        if self.owns_client_session and self.owns_connector:
            # both session and connector are ours. Consequently, the connector shouldn't exist.
            if self.connector:
                await self.connector.close()
            if self.client_session:
                await self.client_session.close()
            self.client_session = None
            self.connector = None
        elif self.owns_client_session:
            # close client session, but don't close borrowed connector.
            await self.client_session.close()
            self.client_session = None
            self.connector = None
        elif self.owns_connector:
            # close connector, but don't close borrowed client session.
            if self.connector:
                await self.connector.close()
            self.client_session = None
            self.connector = None
        else:
            # do nothing. These are borrowed by context manager.
            self.client_session = None
            self.connector = None
            return

    def build_url(self, api: str) -> str:
        """
        Builds the LANraragi server URL.

        Examples:
        - `client.build_url("/api/search")`
        - `client.build_url("/api/archives")`
        """
        return f"{self.lrr_base_url}{api}"

    @override
    async def __aenter__(self: _ApiContextManagerLike) -> _ApiContextManagerLike:
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
            headers: Dict[str, str], params: Query=None, data: Any=None, json_data: Any=None,
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
        - asyncio.TimeoutError: when server doesn't respond in time
        """
        self.logger.debug(f"[{request_type.name}][{url}]")
        retry_count = 0
        while True:
            try:
                match request_type:
                    case http.HTTPMethod.GET:
                        async with (await self._get_session()).get(url=url, headers=headers, params=params, data=data, json=json_data) as async_response:
                            if data:
                                self.logger.warning("GET requests should not include a data field.")
                            return (async_response.status, await async_response.text())
                    case http.HTTPMethod.PUT:
                        if params:
                            self.logger.warning("PUT requests should not include query parameters.")
                        async with (await self._get_session()).put(url=url, headers=headers, params=params, data=data, json=json_data) as async_response:
                            return (async_response.status, await async_response.text())
                    case http.HTTPMethod.POST:
                        if params:
                            self.logger.warning("POST requests should not include query parameters.")
                        async with (await self._get_session()).post(url=url, headers=headers, params=params, data=data, json=json_data) as async_response:
                            return (async_response.status, await async_response.text())
                    case http.HTTPMethod.DELETE:
                        if params:
                            self.logger.warning("DELETE requests should not include query parameters.")
                        async with (await self._get_session()).delete(url=url, headers=headers, params=params, data=data, json=json_data) as async_response:
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
            self, url: str, headers: Dict[str, str], params: Query=None, max_retries: int=0
    ) -> Tuple[int, Union[bytes, str]]:
        """
        Specific to downloading thumbnails from the LANraragi server. (/api/archives/:id/thumbnail)
        """
        self.logger.debug(f"[GET][{url}]")
        retry_count = 0
        while True:
            try:
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
            except (aiohttp.client_exceptions.ClientConnectionError, aiohttp.client_exceptions.ClientOSError, aiohttp.client_exceptions.ClientConnectorError) as aiohttp_error:
                if retry_count >= max_retries:
                    raise aiohttp_error
                retry_count += 1
                self.logger.warning(f"[GET][{url}] encountered connection error ({aiohttp_error}); retrying in {2 ** retry_count} seconds...")
                await asyncio.sleep(2 ** retry_count)
                continue

    async def download_file(
            self, url: str, headers: Dict[str, str], params: Query=None, max_retries: int=0
    ) -> Tuple[int, Union[bytes, str]]:
        """
        Specific to downloading files from the LANraragi server.
        """
        self.logger.debug(f"[GET][{url}]")
        retry_count = 0
        while True:
            try:
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
            except (aiohttp.client_exceptions.ClientConnectionError, aiohttp.client_exceptions.ClientOSError, aiohttp.client_exceptions.ClientConnectorError) as aiohttp_error:
                if retry_count >= max_retries:
                    raise aiohttp_error
                retry_count += 1
                self.logger.warning(f"[GET][{url}] encountered connection error ({aiohttp_error}); retrying in {2 ** retry_count} seconds...")
                await asyncio.sleep(2 ** retry_count)
                continue

__all__ = [
    "ApiContextManager"
]