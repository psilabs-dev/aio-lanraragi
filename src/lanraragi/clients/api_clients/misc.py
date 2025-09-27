import http
import json

import aiohttp
from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.clients.res_processors.misc import _handle_get_available_plugins_response, _handle_use_plugin_response, _process_get_server_info_response
from lanraragi.models.misc import CleanTempFolderResponse, GetAvailablePluginsRequest, GetAvailablePluginsResponse, GetOpdsCatalogRequest, GetOpdsCatalogResponse, GetServerInfoResponse, QueueUrlDownloadRequest, QueueUrlDownloadResponse, RegenerateThumbnailRequest, RegenerateThumbnailResponse, UsePluginAsyncRequest, UsePluginAsyncResponse, UsePluginRequest, UsePluginResponse


class _MiscApiClient(_ApiClient):

    async def get_server_info(self) -> _LRRClientResponse[GetServerInfoResponse]:
        """
        GET /api/info
        """
        url = self.api_context.build_url("/api/info")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            return (_process_get_server_info_response(content), None)
        return (None, _build_err_response(content, status))

    async def get_opds_catalog(self, request: GetOpdsCatalogRequest) -> _LRRClientResponse[GetOpdsCatalogResponse]:
        """
        - GET /api/opds
        - GET /api/opds/:id

        Note: the response returns this as an XML string.
        """
        if request.arcid:
            url = self.api_context.build_url(f"/api/opds/{request.arcid}")
        else:
            url = self.api_context.build_url("/api/opds")
        params = {}
        if request.category:
            params["category"] = request.category
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers, params=params)
        if status == 200:
            return (GetOpdsCatalogResponse(result=content), None)
        return (None, _build_err_response(content, status))

    async def get_available_plugins(self, request: GetAvailablePluginsRequest) -> _LRRClientResponse[GetAvailablePluginsResponse]:
        """
        GET /api/plugins/:type
        """
        url = self.api_context.build_url(f"/api/plugins/{request.type}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            return (_handle_get_available_plugins_response(content), None)
        return (None, _build_err_response(content, status))

    async def use_plugin(self, request: UsePluginRequest) -> _LRRClientResponse[UsePluginResponse]:
        """
        POST /api/plugins/use
        """
        url = self.api_context.build_url("/api/plugins/use")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('key', request.key)
        form_data.add_field('plugin', request.plugin)
        if request.arcid:
            form_data.add_field('id', request.arcid)
        if request.arg:
            form_data.add_field('arg', request.arg)
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, data=form_data)
        if status == 200:
            return (_handle_use_plugin_response(content), None)
        return (None, _build_err_response(content, status))

    async def use_plugin_async(self, request: UsePluginAsyncRequest) -> _LRRClientResponse[UsePluginAsyncResponse]:
        """
        POST /api/plugins/queue
        """
        url = self.api_context.build_url("/api/plugins/queue")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('key', request.key)
        form_data.add_field('plugin', request.plugin)
        if request.arcid:
            form_data.add_field('id', request.arcid)
        if request.arg:
            form_data.add_field('arg', request.arg)
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            job = response_j.get("job")
            return (UsePluginAsyncResponse(job=job), None)
        return (None, _build_err_response(content, status))

    async def clean_temp_folder(self) -> _LRRClientResponse[CleanTempFolderResponse]:
        """
        DELETE /api/tempfolder
        """
        url = self.api_context.build_url("/api/tempfolder")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            newsize = response_j.get("newsize")
            return (CleanTempFolderResponse(newsize=newsize), None)
        return (None, _build_err_response(content, status))

    async def queue_url_download(self, request: QueueUrlDownloadRequest) -> _LRRClientResponse[QueueUrlDownloadResponse]:
        """
        POST /api/download_url
        """
        url = self.api_context.build_url("/api/download_url")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('url', request.url)
        if request.catid:
            form_data.add_field('catid', request.catid)
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            job = response_j.get("job")
            url = response_j.get("url")
            return (QueueUrlDownloadResponse(job=job, url=url), None)
        return (None, _build_err_response(content, status))

    async def regenerate_thumbnails(self, request: RegenerateThumbnailRequest) -> _LRRClientResponse[RegenerateThumbnailResponse]:
        """
        POST /api/regen_thumbs
        """
        url = self.api_context.build_url("/api/regen_thumbs")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('force', request.force)
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            job = response_j.get("job")
            return (RegenerateThumbnailResponse(job=job), None)
        return (None, _build_err_response(content, status))
    pass

__all__ = [
    "_MiscApiClient"
]