from typing import override
from lanraragi.clients.api_context import ApiContextManager
from lanraragi.clients.api_clients.archive import ArchiveApiClient
from lanraragi.clients.api_clients.category import CategoryApiClient
from lanraragi.clients.api_clients.database import DatabaseApiClient
from lanraragi.clients.api_clients.minion import MinionApiClient
from lanraragi.clients.api_clients.misc import MiscApiClient
from lanraragi.clients.api_clients.search import SearchApiClient
from lanraragi.clients.api_clients.shinobu import ShinobuApiClient
from lanraragi.clients.api_clients.tankoubon import TankoubonApiClient

class LRRClient(ApiContextManager):

    @property
    def archive_api(self) -> ArchiveApiClient:
        return self._archive_api
    @archive_api.setter
    def archive_api(self, value: ArchiveApiClient):
        self._archive_api = value

    @property
    def category_api(self) -> CategoryApiClient:
        return self._category_api
    @category_api.setter
    def category_api(self, value: CategoryApiClient):
        self._category_api = value

    @property
    def database_api(self) -> DatabaseApiClient:
        return self._database_api
    @database_api.setter
    def database_api(self, value: DatabaseApiClient):
        self._database_api = value

    @property
    def minion_api(self) -> MinionApiClient:
        return self._minion_api
    @minion_api.setter
    def minion_api(self, value: MinionApiClient):
        self._minion_api = value

    @property
    def misc_api(self) -> MiscApiClient:
        return self._misc_api
    @misc_api.setter
    def misc_api(self, value: MiscApiClient):
        self._misc_api = value

    @property
    def shinobu_api(self) -> ShinobuApiClient:
        return self._shinobu_api
    @shinobu_api.setter
    def shinobu_api(self, value: ShinobuApiClient):
        self._shinobu_api = value

    @property
    def search_api(self) -> SearchApiClient:
        return self._search_api
    @search_api.setter
    def search_api(self, value: SearchApiClient):
        self._search_api = value

    @property
    def tankoubon_api(self) -> TankoubonApiClient:
        return self._tankoubon_api
    @tankoubon_api.setter
    def tankoubon_api(self, value: TankoubonApiClient):
        self._tankoubon_api = value

    @override
    def initialize_api_groups(self):
        self._archive_api = ArchiveApiClient(self)
        self._category_api = CategoryApiClient(self)
        self._database_api = DatabaseApiClient(self)
        self._minion_api = MinionApiClient(self)
        self._misc_api = MiscApiClient(self)
        self._shinobu_api = ShinobuApiClient(self)
        self._search_api = SearchApiClient(self)
        self._tankoubon_api = TankoubonApiClient(self)
