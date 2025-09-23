import http

from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.clients.res_processors.search import (
    _process_get_random_archives_response,
    _process_search_archive_index_response
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

class _SearchApiClient(_ApiClient):

    async def search_archive_index(
            self, request: SearchArchiveIndexRequest
    ) -> _LRRClientResponse[SearchArchiveIndexResponse]:
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
                if isinstance(value, bool):
                    params[key] = str(value).lower()
                else:
                    params[key] = value
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers, params=params)
        if status == 200:
            return (_process_search_archive_index_response(content), None)
        return (None, _build_err_response(content, status))

    async def get_random_archives(
            self, request: GetRandomArchivesRequest
    ) -> _LRRClientResponse[GetRandomArchivesResponse]:
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
                if isinstance(value, bool):
                    params[key] = str(value).lower()
                else:
                    params[key] = value
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers, params=params)
        if status == 200:
            return (_process_get_random_archives_response(content), None)
        return (None, _build_err_response(content, status))

    async def discard_search_cache(self) -> _LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/search/cache
        """
        url = self.api_context.build_url("/api/search/cache")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))

__all__ = [
    "_SearchApiClient"
]