from lanraragi.clients.api_clients.base import ApiClient
from lanraragi.models.generics import LRRClientResponse
from lanraragi.models.base import LanraragiRequest, LanraragiResponse


class TankoubonApiClient(ApiClient):

    async def get_all_tankoubons(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        GET /api/tankoubons
        """
        # TODO: skip tankoubons for now.
        raise NotImplementedError

    async def get_tankoubon(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        GET /api/tankoubons/:id
        """
        # TODO: skip tankoubons for now.
        raise NotImplementedError

    async def create_tankoubon(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        PUT /api/tankoubons
        """
        # TODO: skip tankoubons for now.
        raise NotImplementedError

    async def update_tankoubon(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        PUT /api/tankoubons/:id
        """
        # TODO: skip tankoubons for now.
        raise NotImplementedError

    async def add_archive_to_tankoubon(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        PUT /api/tankoubons/:id/:archive
        """
        # TODO: skip tankoubons for now.
        raise NotImplementedError

    async def remove_archive_from_tankoubon(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/tankoubons/:id/:archive
        """
        # TODO: skip tankoubons for now.
        raise NotImplementedError

    async def delete_tankoubon(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/tankoubons/:id
        """
        # TODO: skip tankoubons for now.
        raise NotImplementedError