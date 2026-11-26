import http
import json

from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.models.stamp import (
    AddStampRequest,
    AddStampResponse,
    DeleteStampRequest,
    DeleteStampResponse,
    GetStampedPagesRequest,
    GetStampedPagesResponse,
    GetStampRequest,
    GetStampResponse,
    GetStampsByPageRequest,
    GetStampsByPageResponse,
    UpdateStampRequest,
    UpdateStampResponse,
)


class _StampApiClient(_ApiClient):

    async def get_stamp(self, request: GetStampRequest) -> _LRRClientResponse[GetStampResponse]:
        """
        GET /api/stamps/:id
        """
        url = self.api_context.build_url(f"/api/stamps/{request.stamp_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            payload = json.loads(content)
            stamp = payload.get("result", payload)
            return (GetStampResponse(result={"id": request.stamp_id, **stamp}), None)
        return (None, _build_err_response(content, status))

    async def get_stamps_by_page(self, request: GetStampsByPageRequest) -> _LRRClientResponse[GetStampsByPageResponse]:
        """
        GET /api/archives/:id/stamps/:index
        """
        url = self.api_context.build_url(f"/api/archives/{request.arcid}/stamps/{request.index}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            return (GetStampsByPageResponse.model_validate_json(content), None)
        return (None, _build_err_response(content, status))

    async def get_stamped_pages(self, request: GetStampedPagesRequest) -> _LRRClientResponse[GetStampedPagesResponse]:
        """
        GET /api/archives/:id/stamps
        """
        url = self.api_context.build_url(f"/api/archives/{request.arcid}/stamps")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            return (GetStampedPagesResponse.model_validate_json(content), None)
        return (None, _build_err_response(content, status))

    async def add_stamp(self, request: AddStampRequest) -> _LRRClientResponse[AddStampResponse]:
        """
        PUT /api/archives/:id/stamps/:index
        """
        url = self.api_context.build_url(f"/api/archives/{request.arcid}/stamps/{request.index}")
        params = {}
        if request.content is not None:
            params["content"] = request.content
        if request.position is not None:
            params["position"] = request.position
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers, params=params)
        if status == 200:
            response_j = json.loads(content)
            if response_j.get("success") == 1:
                return (AddStampResponse(stamp_id=response_j["stamp_id"]), None)
        return (None, _build_err_response(content, status))

    async def update_stamp(self, request: UpdateStampRequest) -> _LRRClientResponse[UpdateStampResponse]:
        """
        PUT /api/stamps/:id
        """
        url = self.api_context.build_url(f"/api/stamps/{request.stamp_id}")
        params = {}
        if request.content is not None:
            params["content"] = request.content
        if request.position is not None:
            params["position"] = request.position
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers, params=params)
        if status == 200:
            response_j = json.loads(content)
            if response_j.get("success") == 1:
                return (UpdateStampResponse(success_message=response_j.get("successMessage")), None)
        return (None, _build_err_response(content, status))

    async def delete_stamp(self, request: DeleteStampRequest) -> _LRRClientResponse[DeleteStampResponse]:
        """
        DELETE /api/stamps/:id
        """
        url = self.api_context.build_url(f"/api/stamps/{request.stamp_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            if response_j.get("success") == 1:
                return (DeleteStampResponse(success_message=response_j.get("successMessage")), None)
        return (None, _build_err_response(content, status))

__all__ = [
    "_StampApiClient"
]
