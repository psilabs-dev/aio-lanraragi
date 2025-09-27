import http
import json

import aiohttp
from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.clients.res_processors.category import _process_get_all_categories_response, _process_get_category_response
from lanraragi.models.base import LanraragiResponse
from lanraragi.models.category import AddArchiveToCategoryRequest, AddArchiveToCategoryResponse, CreateCategoryRequest, CreateCategoryResponse, DeleteCategoryRequest, DisableBookmarkLinkResponse, GetAllCategoriesResponse, GetBookmarkLinkResponse, GetCategoryRequest, GetCategoryResponse, RemoveArchiveFromCategoryRequest, UpdateBookmarkLinkRequest, UpdateBookmarkLinkResponse, UpdateCategoryRequest, UpdateCategoryResponse


class _CategoryApiClient(_ApiClient):

    async def get_all_categories(self) -> _LRRClientResponse[GetAllCategoriesResponse]:
        """
        GET /api/categories
        """
        url = self.api_context.build_url("/api/categories")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            return (_process_get_all_categories_response(content), None)
        return (None, _build_err_response(content, status))

    async def get_category(self, request: GetCategoryRequest) -> _LRRClientResponse[GetCategoryResponse]:
        """
        GET /api/categories/:id
        """
        url = self.api_context.build_url(f"/api/categories/{request.category_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            return (_process_get_category_response(content), None)
        return (None, _build_err_response(content, status))

    async def create_category(self, request: CreateCategoryRequest) -> _LRRClientResponse[CreateCategoryResponse]:
        """
        PUT /api/categories
        """
        url = self.api_context.build_url("/api/categories")
        form_data = aiohttp.FormData(quote_fields=False)
        if request.pinned is not None:
            form_data.add_field('pinned', request.pinned)
        form_data.add_field('name', request.name)
        if request.search is not None:
            form_data.add_field('search', request.search)
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (CreateCategoryResponse(category_id=category_id), None)
        return (None, _build_err_response(content, status))

    async def update_category(self, request: UpdateCategoryRequest) -> _LRRClientResponse[UpdateCategoryResponse]:
        """
        PUT /api/categories/:id
        """
        url = self.api_context.build_url(f"/api/categories/{request.category_id}")
        form_data = aiohttp.FormData(quote_fields=False)
        if request.pinned is not None:
            form_data.add_field('pinned', request.pinned)
        if request.name is not None:
            form_data.add_field('name', request.name)
        if request.search is not None:
            form_data.add_field('search', request.search)
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (UpdateCategoryResponse(category_id=category_id), None)
        return (None, _build_err_response(content, status))

    async def delete_category(self, request: DeleteCategoryRequest) -> _LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/categories/:id
        """
        url = self.api_context.build_url(f"/api/categories/{request.category_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))

    async def add_archive_to_category(self, request: AddArchiveToCategoryRequest) -> _LRRClientResponse[AddArchiveToCategoryResponse]:
        """
        PUT /api/categories/:id/:archive
        """
        url = self.api_context.build_url(f"/api/categories/{request.category_id}/{request.arcid}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            success_message = response_j.get("successMessage")
            return (AddArchiveToCategoryResponse(success_message=success_message), None)
        return (None, _build_err_response(content, status))

    async def remove_archive_from_category(self, request: RemoveArchiveFromCategoryRequest) -> _LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/categories/:id/:archive
        """
        url = self.api_context.build_url(f"/api/categories/{request.category_id}/{request.arcid}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))

    async def get_bookmark_link(self) -> _LRRClientResponse[GetBookmarkLinkResponse]:
        """
        GET /api/categories/bookmark_link

        If bookmark link is disabled, the response will be None.
        """
        url = self.api_context.build_url("/api/categories/bookmark_link")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (GetBookmarkLinkResponse(category_id=category_id), None)
        return (None, _build_err_response(content, status))

    async def update_bookmark_link(self, request: UpdateBookmarkLinkRequest) -> _LRRClientResponse[UpdateBookmarkLinkResponse]:
        """
        PUT /api/categories/bookmark_link/:id
        """
        url = self.api_context.build_url(f"/api/categories/bookmark_link/{request.category_id}")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('id', request.category_id)
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (UpdateBookmarkLinkResponse(category_id=category_id), None)
        return (None, _build_err_response(content, status))

    async def disable_bookmark_feature(self) -> _LRRClientResponse[DisableBookmarkLinkResponse]:
        """
        DELETE /api/categories/bookmark_link
        """
        url = self.api_context.build_url("/api/categories/bookmark_link")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (DisableBookmarkLinkResponse(category_id=category_id), None)
        return (None, _build_err_response(content, status))

__all__ = [
    "_CategoryApiClient"
]