from typing import override
from lanraragi.clients.api_context import ApiContextManager
from lanraragi.clients.api_clients.archive import _ArchiveApiClient
from lanraragi.clients.api_clients.category import _CategoryApiClient
from lanraragi.clients.api_clients.database import _DatabaseApiClient
from lanraragi.clients.api_clients.minion import _MinionApiClient
from lanraragi.clients.api_clients.misc import _MiscApiClient
from lanraragi.clients.api_clients.search import _SearchApiClient
from lanraragi.clients.api_clients.shinobu import _ShinobuApiClient
from lanraragi.clients.api_clients.tankoubon import _TankoubonApiClient

class LRRClient(ApiContextManager):
    """
    LANraragi API client and context manager.
    Configured by passing in LANraragi server address and API key.

    ## Example
    ```python
    from lanraragi import LRRClient
    import asyncio

    async def main():
        async with LRRClient("http://localhost:3000", lrr_api_key="lanraragi") as lrr:
            response, error = await lrr.misc_api.get_server_info()
            if error:
                raise Exception(f"Encountered error while getting server info: {error.error}")
            print(response.name)
    ```

    LRRClient API calls are grouped into the following.
    - archive_api
    - category_api
    - database_api
    - minion_api
    - misc_api
    - shinobu_api
    - search_api
    - tankoubon_api
    """

    @property
    def archive_api(self) -> _ArchiveApiClient:
        """
        https://sugoi.gitbook.io/lanraragi/api-documentation/archive-api
        """
        return self._archive_api
    @archive_api.setter
    def archive_api(self, value: _ArchiveApiClient):
        self._archive_api = value

    @property
    def category_api(self) -> _CategoryApiClient:
        """
        https://sugoi.gitbook.io/lanraragi/api-documentation/category-api
        """
        return self._category_api
    @category_api.setter
    def category_api(self, value: _CategoryApiClient):
        self._category_api = value

    @property
    def database_api(self) -> _DatabaseApiClient:
        """
        https://sugoi.gitbook.io/lanraragi/api-documentation/database-api
        """
        return self._database_api
    @database_api.setter
    def database_api(self, value: _DatabaseApiClient):
        self._database_api = value

    @property
    def minion_api(self) -> _MinionApiClient:
        """
        https://sugoi.gitbook.io/lanraragi/api-documentation/minion-api
        """
        return self._minion_api
    @minion_api.setter
    def minion_api(self, value: _MinionApiClient):
        self._minion_api = value

    @property
    def misc_api(self) -> _MiscApiClient:
        """
        https://sugoi.gitbook.io/lanraragi/api-documentation/miscellaneous-other-api
        """
        return self._misc_api
    @misc_api.setter
    def misc_api(self, value: _MiscApiClient):
        self._misc_api = value

    @property
    def shinobu_api(self) -> _ShinobuApiClient:
        """
        https://sugoi.gitbook.io/lanraragi/api-documentation/shinobu-api
        """
        return self._shinobu_api
    @shinobu_api.setter
    def shinobu_api(self, value: _ShinobuApiClient):
        self._shinobu_api = value

    @property
    def search_api(self) -> _SearchApiClient:
        """
        https://sugoi.gitbook.io/lanraragi/api-documentation/search-api
        """
        return self._search_api
    @search_api.setter
    def search_api(self, value: _SearchApiClient):
        self._search_api = value

    @property
    def tankoubon_api(self) -> _TankoubonApiClient:
        """
        https://sugoi.gitbook.io/lanraragi/api-documentation/tankoubon-api
        """
        return self._tankoubon_api
    @tankoubon_api.setter
    def tankoubon_api(self, value: _TankoubonApiClient):
        self._tankoubon_api = value

    @override
    def initialize_api_groups(self):
        self._archive_api = _ArchiveApiClient(self)
        self._category_api = _CategoryApiClient(self)
        self._database_api = _DatabaseApiClient(self)
        self._minion_api = _MinionApiClient(self)
        self._misc_api = _MiscApiClient(self)
        self._shinobu_api = _ShinobuApiClient(self)
        self._search_api = _SearchApiClient(self)
        self._tankoubon_api = _TankoubonApiClient(self)

__all__ = [
    "LRRClient"
]
