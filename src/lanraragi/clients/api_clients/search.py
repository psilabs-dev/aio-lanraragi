import http

from lanraragi.clients.api_clients.base import ApiClient
from lanraragi.clients.utils import build_err_response
from lanraragi.models.generics import LRRClientResponse
from lanraragi.clients.res_processors.search import (
    process_get_random_archives_response,
    process_search_archive_index_response
)
from lanraragi.models.base import (
    LanraragiResponse,
)
from lanraragi.models.search import (
    GetRandomArchivesRequest, 
    GetRandomArchivesResponse, 
    SearchArchiveIndexRequest, 
    SearchArchiveIndexResponse, 
)

class SearchApiClient(ApiClient):

    async def search_archive_index(
            self, request: SearchArchiveIndexRequest
    ) -> LRRClientResponse[SearchArchiveIndexResponse]:
        """
        GET /api/search
        """
        url = self.api_context.build_url("/api/search")
        params = {}
        for key, value in [
            ("category", request.category),
            ("filter", request.search_filter),
            ("start", request.start),
            ("sortby", request.sortby),
            ("order", request.order),
            ("groupby_tanks", request.groupby_tanks),
        ]:
            if value:
                params[key] = value
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.api_context.headers, params=params)
        if status == 200:
            return (process_search_archive_index_response(content), None)
        return (None, build_err_response(content, status))

    async def get_random_archives(
            self, request: GetRandomArchivesRequest
    ) -> LRRClientResponse[GetRandomArchivesResponse]:
        """
        GET /api/search/random
        """
        url = self.api_context.build_url("/api/search/random")
        params = {}
        for key, value in [
            ("category", request.category),
            ("filter", request.filter),
            ("count", request.count),
            ("newonly", request.newonly),
            ("untaggedonly", request.untaggedonly),
            ("groupby_tanks", request.groupby_tanks),
        ]:
            if value:
                params[key] = value
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.api_context.headers, params=params)
        if status == 200:
            return (process_get_random_archives_response(content), None)
        return (None, build_err_response(content, status))

    async def discard_search_cache(self) -> LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/search/cache
        """
        url = self.api_context.build_url("/api/search/cache")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.api_context.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, build_err_response(content, status))
