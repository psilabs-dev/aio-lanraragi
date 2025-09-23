import http
import json

import aiohttp
from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.res_processors.tankoubon import _handle_get_all_tankoubons_response, _handle_get_tankoubon_response
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.models.tankoubon import (
    AddArchiveToTankoubonRequest,
    AddArchiveToTankoubonResponse,
    CreateTankoubonRequest,
    CreateTankoubonResponse,
    DeleteTankoubonRequest,
    DeleteTankoubonResponse,
    GetAllTankoubonsRequest,
    GetAllTankoubonsResponse,
    GetTankoubonRequest,
    GetTankoubonResponse,
    RemoveArchiveFromTankoubonRequest,
    RemoveArchiveFromTankoubonResponse,
    UpdateTankoubonRequest,
    UpdateTankoubonResponse
)

class _TankoubonApiClient(_ApiClient):

    async def get_all_tankoubons(self, request: GetAllTankoubonsRequest) -> _LRRClientResponse[GetAllTankoubonsResponse]:
        """
        GET /api/tankoubons
        """
        url = self.api_context.build_url("/api/tankoubons")
        params = {}
        if request.page:
            params["page"] = request.page
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers, params=params)
        if status == 200:
            return (_handle_get_all_tankoubons_response(content), None)
        return (None, _build_err_response(content, status))

    async def get_tankoubon(self, request: GetTankoubonRequest) -> _LRRClientResponse[GetTankoubonResponse]:
        """
        GET /api/tankoubons/:id
        """
        url = self.api_context.build_url(f"/api/tankoubons/{request.tank_id}")
        params = {}
        if request.include_full_data:
            params["include_full_data"] = request.include_full_data
        if request.page:
            params["page"] = request.page
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers, params=params)
        if status == 200:
            return (_handle_get_tankoubon_response(content, request.include_full_data), None)
        return (None, _build_err_response(content, status))

    async def create_tankoubon(self, request: CreateTankoubonRequest) -> _LRRClientResponse[CreateTankoubonResponse]:
        """
        PUT /api/tankoubons
        """
        url = self.api_context.build_url("/api/tankoubons")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('name', request.name)
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            return (CreateTankoubonResponse(tank_id=response_j.get("tankoubon_id")), None)
        return (None, _build_err_response(content, status))

    async def update_tankoubon(self, request: UpdateTankoubonRequest) -> _LRRClientResponse[UpdateTankoubonResponse]:
        """
        PUT /api/tankoubons/:id
        """
        url = self.api_context.build_url(f"/api/tankoubons/{request.tank_id}")
        payload = {}
        if request.archives:
            payload["archives"] = request.archives
        if request.metadata:
            payload["metadata"] = request.metadata.model_dump(exclude_none=True)
        status, content = await self.api_context.handle_request(
            http.HTTPMethod.PUT, 
            url, 
            self.headers, 
            json_data=payload
        )
        if status == 200:
            response_j = json.loads(content)
            return (UpdateTankoubonResponse(success_message=response_j.get("successMessage")), None)
        return (None, _build_err_response(content, status))

    async def add_archive_to_tankoubon(self, request: AddArchiveToTankoubonRequest) -> _LRRClientResponse[AddArchiveToTankoubonResponse]:
        """
        PUT /api/tankoubons/:id/:archive
        """
        url = self.api_context.build_url(f"/api/tankoubons/{request.tank_id}/{request.arcid}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            return (AddArchiveToTankoubonResponse(success_message=response_j.get("successMessage")), None)
        return (None, _build_err_response(content, status))

    async def remove_archive_from_tankoubon(self, request: RemoveArchiveFromTankoubonRequest) -> _LRRClientResponse[RemoveArchiveFromTankoubonResponse]:
        """
        DELETE /api/tankoubons/:id/:archive
        """
        url = self.api_context.build_url(f"/api/tankoubons/{request.tank_id}/{request.arcid}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            return (RemoveArchiveFromTankoubonResponse(success_message=response_j.get("successMessage")), None)
        return (None, _build_err_response(content, status))

    async def delete_tankoubon(self, request: DeleteTankoubonRequest) -> _LRRClientResponse[DeleteTankoubonResponse]:
        """
        DELETE /api/tankoubons/:id
        """
        url = self.api_context.build_url(f"/api/tankoubons/{request.tank_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            return (DeleteTankoubonResponse(success_message=response_j.get("successMessage")), None)
        return (None, _build_err_response(content, status))

__all__ = [
    "_TankoubonApiClient"
]