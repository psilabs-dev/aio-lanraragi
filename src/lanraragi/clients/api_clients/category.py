

import http
import json

import aiohttp
from lanraragi.clients.api_clients.base import ApiClient
from lanraragi.clients.utils import build_err_response
from lanraragi.models.generics import LRRClientResponse
from lanraragi.clients.res_processors.category import process_get_all_categories_response, process_get_category_response
from lanraragi.models.base import LanraragiResponse
from lanraragi.models.category import AddArchiveToCategoryRequest, AddArchiveToCategoryResponse, CreateCategoryRequest, CreateCategoryResponse, DeleteCategoryRequest, DisableBookmarkLinkResponse, GetAllCategoriesResponse, GetBookmarkLinkResponse, GetCategoryRequest, GetCategoryResponse, RemoveArchiveFromCategoryRequest, UpdateBookmarkLinkRequest, UpdateBookmarkLinkResponse, UpdateCategoryRequest, UpdateCategoryResponse


class CategoryApiClient(ApiClient):

    async def get_all_categories(self) -> LRRClientResponse[GetAllCategoriesResponse]:
        """
        GET /api/categories
        """
        url = self.api_context.build_url("/api/categories")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.api_context.headers)
        if status == 200:
            return (process_get_all_categories_response(content), None)
        return (None, build_err_response(content, status))

    async def get_category(self, request: GetCategoryRequest) -> LRRClientResponse[GetCategoryResponse]:
        """
        GET /api/categories/:id
        """
        url = self.api_context.build_url(f"/api/categories/{request.id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.api_context.headers)
        if status == 200:
            return (process_get_category_response(content), None)
        return (None, build_err_response(content, status))

    async def create_category(self, request: CreateCategoryRequest) -> LRRClientResponse[CreateCategoryResponse]:
        """
        PUT /api/categories
        """
        url = self.api_context.build_url("/api/categories")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('pinned', request.pinned)
        form_data.add_field('name', request.name)
        form_data.add_field('search', request.search)
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.api_context.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (CreateCategoryResponse(category_id=category_id), None)
        return (None, build_err_response(content, status))

    async def update_category(self, request: UpdateCategoryRequest) -> LRRClientResponse[UpdateCategoryResponse]:
        """
        PUT /api/categories/:id
        """
        url = self.api_context.build_url(f"/api/categories/{request.id}")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('pinned', request.pinned)
        form_data.add_field('name', request.name)
        form_data.add_field('search', request.search)
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.api_context.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (UpdateCategoryResponse(category_id=category_id), None)
        return (None, build_err_response(content, status))

    async def delete_category(self, request: DeleteCategoryRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/categories/:id
        """
        url = self.api_context.build_url(f"/api/categories/{request.id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.api_context.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, build_err_response(content, status))

    async def add_archive_to_category(self, request: AddArchiveToCategoryRequest) -> LRRClientResponse[AddArchiveToCategoryResponse]:
        """
        PUT /api/categories/:id/:archive
        """
        url = self.api_context.build_url(f"/api/categories/{request.id}/{request.archive}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.api_context.headers)
        if status == 200:
            response_j = json.loads(content)
            success_message = response_j.get("success_message")
            return (AddArchiveToCategoryResponse(success_message=success_message), None)
        return (None, build_err_response(content, status))

    async def remove_archive_from_category(self, request: RemoveArchiveFromCategoryRequest) -> LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/categories/:id/:archive
        """
        url = self.api_context.build_url(f"/api/categories/{request.id}/{request.archive}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.api_context.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, build_err_response(content, status))

    async def get_bookmark_link(self) -> LRRClientResponse[GetBookmarkLinkResponse]:
        """
        GET /api/categories/bookmark_link
        """
        url = self.api_context.build_url("/api/categories/bookmark_link")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.api_context.headers)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (GetBookmarkLinkResponse(category_id=category_id), None)
        return (None, build_err_response(content, status))

    async def update_bookmark_link(self, request: UpdateBookmarkLinkRequest) -> LRRClientResponse[UpdateBookmarkLinkResponse]:
        """
        PUT /api/categories/bookmark_link/:id
        """
        url = self.api_context.build_url(f"/api/categories/bookmark_link/{request.id}")
        form_data = aiohttp.FormData(quote_fields=False)
        form_data.add_field('id', request.id)
        status, content = await self.api_context.handle_request(http.HTTPMethod.PUT, url, self.api_context.headers, data=form_data)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (UpdateBookmarkLinkResponse(category_id=category_id), None)
        return (None, build_err_response(content, status))

    async def disable_bookmark_feature(self) -> LRRClientResponse[DisableBookmarkLinkResponse]:
        """
        DELETE /api/categories/bookmark_link
        """
        url = self.api_context.build_url("/api/categories/bookmark_link")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.api_context.headers)
        if status == 200:
            response_j = json.loads(content)
            category_id = response_j.get("category_id")
            return (DisableBookmarkLinkResponse(category_id=category_id), None)
        return (None, build_err_response(content, status))
