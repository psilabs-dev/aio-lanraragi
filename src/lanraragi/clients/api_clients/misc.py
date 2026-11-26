import http
import json

import aiohttp

from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.res_processors.misc import (
    _handle_get_available_plugins_response,
    _process_get_server_info_response,
)
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.models.misc import (
    CleanTempFolderResponse,
    CreateRegistryRequest,
    CreateRegistryResponse,
    GetAvailablePluginsRequest,
    GetAvailablePluginsResponse,
    GetDefaultRegistryResponse,
    GetOpdsCatalogRequest,
    GetOpdsCatalogResponse,
    GetOpdsPageRequest,
    GetOpdsPageResponse,
    GetRegistryResponse,
    GetServerInfoResponse,
    InstallPluginRequest,
    InstallPluginResponse,
    ListRegistriesResponse,
    QueueUrlDownloadRequest,
    QueueUrlDownloadResponse,
    RefreshRegistryResponse,
    RegenerateThumbnailRequest,
    RegenerateThumbnailResponse,
    RegistryConfig,
    RemoveDefaultRegistryResponse,
    UpdateDefaultRegistryResponse,
    UpdateRegistryRequest,
    UpdateRegistryResponse,
    UsePluginAsyncRequest,
    UsePluginAsyncResponse,
    UsePluginRawResponse,
    UsePluginRequest,
    UsePluginResponse,
)


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

    async def get_opds_page(self, request: GetOpdsPageRequest) -> _LRRClientResponse[GetOpdsPageResponse]:
        url = self.api_context.build_url(f"/api/opds/{request.arcid}/pse")
        params = {"page": request.page} if request.page is not None else None
        status, content = await self.api_context.download_file(url, self.headers, params=params)
        if status == 200:
            return (GetOpdsPageResponse(data=content), None)
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
        params = {"plugin": request.plugin}
        if request.arcid:
            params["id"] = request.arcid
        if request.arg:
            params["arg"] = request.arg
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, params=params)
        if status == 200:
            raw = UsePluginRawResponse.model_validate_json(content)
            if raw.success == 0:
                error_message = raw.error or (raw.data.get("error") if raw.data else None) or "Plugin execution failed."
                return (None, LanraragiErrorResponse(error=error_message, status=status))
            return (UsePluginResponse(type=raw.type, data=raw.data), None)
        return (None, _build_err_response(content, status))

    async def use_plugin_async(self, request: UsePluginAsyncRequest) -> _LRRClientResponse[UsePluginAsyncResponse]:
        """
        POST /api/plugins/queue
        """
        url = self.api_context.build_url("/api/plugins/queue")
        params = {"plugin": request.plugin}
        if request.arcid:
            params["id"] = request.arcid
        if request.arg is not None:
            params["arg"] = request.arg
        if request.priority is not None:
            params["priority"] = request.priority
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, params=params)
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
        params = {}
        if request.force is not None:
            params["force"] = str(request.force).lower()
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, params=params)
        if status == 200:
            response_j = json.loads(content)
            job = response_j.get("job")
            return (RegenerateThumbnailResponse(job=job), None)
        return (None, _build_err_response(content, status))

    async def list_registries(self) -> _LRRClientResponse[ListRegistriesResponse]:
        """
        GET /api/registries
        """
        url = self.api_context.build_url("/api/registries")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            registries = [RegistryConfig.model_validate(r) for r in response_j.get("registries", [])]
            return (ListRegistriesResponse(registries=registries), None)
        return (None, _build_err_response(content, status))

    async def create_registry(self, request: CreateRegistryRequest) -> _LRRClientResponse[CreateRegistryResponse]:
        """
        POST /api/registries
        """
        url = self.api_context.build_url("/api/registries")
        body = request.model_dump(exclude={"max_retries"}, exclude_none=True)
        status, content = await self.api_context.handle_request(
            http.HTTPMethod.POST, url, self.headers, json_data=body
        )
        if status == 200:
            response_j = json.loads(content)
            return (CreateRegistryResponse(id=response_j["id"]), None)
        return (None, _build_err_response(content, status))

    async def get_registry(self, registry_id: str) -> _LRRClientResponse[GetRegistryResponse]:
        """
        GET /api/registries/{id}
        """
        url = self.api_context.build_url(f"/api/registries/{registry_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            registry = RegistryConfig.model_validate(response_j.get("registry"))
            return (GetRegistryResponse(registry=registry), None)
        return (None, _build_err_response(content, status))

    async def update_registry(self, registry_id: str, request: UpdateRegistryRequest) -> _LRRClientResponse[UpdateRegistryResponse]:
        """
        PUT /api/registries/{id}
        """
        url = self.api_context.build_url(f"/api/registries/{registry_id}")
        body = request.model_dump(exclude={"max_retries"}, exclude_none=True)
        status, content = await self.api_context.handle_request(
            http.HTTPMethod.PUT, url, self.headers, json_data=body
        )
        if status == 200:
            response_j = json.loads(content)
            return (UpdateRegistryResponse(
                id=response_j["id"],
            ), None)
        return (None, _build_err_response(content, status))

    async def delete_registry(self, registry_id: str) -> _LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/registries/{id}
        """
        url = self.api_context.build_url(f"/api/registries/{registry_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))

    async def get_default_registry(self) -> _LRRClientResponse[GetDefaultRegistryResponse]:
        """
        GET /api/registries/default_registry
        """
        url = self.api_context.build_url("/api/registries/default_registry")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            return (GetDefaultRegistryResponse(id=response_j["id"]), None)
        return (None, _build_err_response(content, status))

    async def update_default_registry(self, registry_id: str) -> _LRRClientResponse[UpdateDefaultRegistryResponse]:
        """
        PUT /api/registries/default_registry/{id}
        """
        url = self.api_context.build_url(f"/api/registries/default_registry/{registry_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            return (UpdateDefaultRegistryResponse(id=response_j["id"]), None)
        return (None, _build_err_response(content, status))

    async def remove_default_registry(self) -> _LRRClientResponse[RemoveDefaultRegistryResponse]:
        """
        DELETE /api/registries/default_registry
        """
        url = self.api_context.build_url("/api/registries/default_registry")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            return (RemoveDefaultRegistryResponse(id=response_j["id"]), None)
        return (None, _build_err_response(content, status))

    async def refresh_registry(self, registry_id: str) -> _LRRClientResponse[RefreshRegistryResponse]:
        """
        POST /api/registries/{id}/refresh
        """
        url = self.api_context.build_url(f"/api/registries/{registry_id}/refresh")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            return (RefreshRegistryResponse(index=response_j.get("index")), None)
        return (None, _build_err_response(content, status))

    async def install_plugin(self, request: InstallPluginRequest) -> _LRRClientResponse[InstallPluginResponse]:
        """
        POST /api/plugins/install
        """
        url = self.api_context.build_url("/api/plugins/install")
        body = request.model_dump(exclude={"max_retries"}, exclude_none=True)
        status, content = await self.api_context.handle_request(
            http.HTTPMethod.POST, url, self.headers, json_data=body
        )
        if status == 200:
            return (InstallPluginResponse.model_validate_json(content), None)
        return (None, _build_err_response(content, status))

    async def uninstall_plugin(self, namespace: str) -> _LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/plugins/installed/{namespace}
        """
        url = self.api_context.build_url(f"/api/plugins/installed/{namespace}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))



__all__ = [
    "_MiscApiClient"
]
