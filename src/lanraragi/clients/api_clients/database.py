import http
import json
from lanraragi.clients.api_clients.base import ApiClient
from lanraragi.clients.utils import build_err_response
from lanraragi.models.generics import LRRClientResponse
from lanraragi.clients.res_processors.database import process_get_database_stats_response
from lanraragi.models.base import LanraragiRequest, LanraragiResponse
from lanraragi.models.database import CleanDatabaseResponse, GetDatabaseStatsRequest, GetDatabaseStatsResponse


class DatabaseApiClient(ApiClient):
    
    async def get_database_stats(self, request: GetDatabaseStatsRequest) -> LRRClientResponse[GetDatabaseStatsResponse]:
        """
        GET /api/database/stats
        """
        url = self.api_context.build_url("/api/database/stats")
        params = {}
        params["minweight"] = request.minweight
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.api_context.headers, params=params)
        if status == 200:
            return (process_get_database_stats_response(content), None)
        return (None, build_err_response(content, status))

    async def clean_database(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        POST /api/database/clean
        """
        url = self.build_url("/api/database/clean")
        status, content = await self.handle_request(http.HTTPMethod.POST, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            deleted = response_j.get("deleted")
            unlinked = response_j.get("unlinked")
            return (CleanDatabaseResponse(deleted=deleted, unlinked=unlinked), None)
        return (None, build_err_response(content, status))

    async def drop_database(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        POST /api/database/drop
        """
        url = self.build_url("/api/database/drop")
        status, content = await self.handle_request(http.HTTPMethod.POST, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, build_err_response(content, status))

    async def get_database_backup(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        GET /api/database/backup
        """
        # TODO: this needs some thought.
        raise NotImplementedError

    async def clear_all_new_flags(self, request: LanraragiRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/database/isnew
        """
        url = self.build_url("/api/database/isnew")
        status, content = await self.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, build_err_response(content, status))