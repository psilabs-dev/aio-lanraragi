import http
import json
from typing import Any

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
    GetOpdsCatalogRequest,
    GetOpdsCatalogResponse,
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
    UpdatePluginConfigRequest,
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
        if request.arg:
            params["arg"] = request.arg
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
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('force', request.force)
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, data=form_data)
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
        body: dict[str, str] = {"name": request.name, "type": request.type}
        if request.provider:
            body["provider"] = request.provider
        if request.url:
            body["url"] = request.url
        if request.ref:
            body["ref"] = request.ref
        if request.path:
            body["path"] = request.path
        status, content = await self.api_context.handle_request(
            http.HTTPMethod.POST, url, self.headers, json_data=body
        )
        if status == 200:
            response_j = json.loads(content)
            registry = RegistryConfig.model_validate(response_j.get("registry"))
            return (CreateRegistryResponse(id=response_j["id"], registry=registry), None)
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
            return (GetRegistryResponse(id=response_j["id"], registry=registry), None)
        return (None, _build_err_response(content, status))

    async def update_registry(self, registry_id: str, request: UpdateRegistryRequest) -> _LRRClientResponse[UpdateRegistryResponse]:
        """
        PUT /api/registries/{id}
        """
        url = self.api_context.build_url(f"/api/registries/{registry_id}")
        body: dict[str, str] = {}
        if request.name is not None:
            body["name"] = request.name
        if request.type is not None:
            body["type"] = request.type
        if request.provider is not None:
            body["provider"] = request.provider
        if request.url is not None:
            body["url"] = request.url
        if request.ref is not None:
            body["ref"] = request.ref
        if request.path is not None:
            body["path"] = request.path
        status, content = await self.api_context.handle_request(
            http.HTTPMethod.PUT, url, self.headers, json_data=body
        )
        if status == 200:
            response_j = json.loads(content)
            registry = RegistryConfig.model_validate(response_j.get("registry"))
            return (UpdateRegistryResponse(
                id=response_j["id"],
                registry=registry,
                index_cleared=response_j.get("index_cleared", False),
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
        body: dict[str, Any] = {"namespace": request.namespace, "registry": request.registry}
        if request.force is not None:
            body["force"] = request.force
        status, content = await self.api_context.handle_request(
            http.HTTPMethod.POST, url, self.headers, json_data=body
        )
        if status == 200:
            response_j = json.loads(content)
            return (InstallPluginResponse(
                name=response_j["name"],
                namespace=response_j["namespace"],
                version=response_j["version"],
                registry=response_j["registry"],
            ), None)
        return (None, _build_err_response(content, status))

    async def uninstall_plugin(self, namespace: str) -> _LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/plugins/{namespace}
        """
        url = self.api_context.build_url(f"/api/plugins/installed/{namespace}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            if response_j.get("success") == 0:
                return (None, LanraragiErrorResponse(error=response_j.get("error", ""), status=status))
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))

    async def update_plugin_config(self, namespace: str, request: UpdatePluginConfigRequest) -> _LRRClientResponse[LanraragiResponse]:
        """
        PUT /api/plugins/installed/{namespace}/config
        """
        url = self.api_context.build_url(f"/api/plugins/installed/{namespace}/config")
        body = {}
        if request.enabled is not None:
            body["enabled"] = request.enabled
        if request.hidden is not None:
            body["hidden"] = request.hidden
        if request.priority is not None:
            body["priority"] = request.priority
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers, json_data=body)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))

__all__ = [
    "_MiscApiClient"
]
