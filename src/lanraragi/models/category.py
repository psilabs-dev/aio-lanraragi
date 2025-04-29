from typing import List
from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse

class GetAllCategoriesResponseRecord(BaseModel):
    archives: List[str] = Field(...)
    id: str = Field(...)
    name: str = Field(...)
    pinned: bool = Field(...)
    search: str = Field(...)

class GetAllCategoriesResponse(LanraragiResponse):
    data: List[str] = Field(...)

class GetCategoryRequest(LanraragiRequest):
    id: str = Field(...)

class GetCategoryResponse(LanraragiResponse):
    archives: List[str] = Field(...)
    id: str = Field(...)
    name: str = Field(...)
    pinned: bool = Field(...)
    search: str = Field(...)

class CreateCategoryRequest(LanraragiRequest):
    pinned: bool = Field(False)
    name: str = Field(...)
    search: str = Field(...)

class CreateCategoryResponse(LanraragiResponse):
    category_id: str = Field(...)

class UpdateCategoryRequest(LanraragiRequest):
    id: str = Field(...)
    pinned: bool = Field(False)
    name: str = Field(...)
    search: str = Field(...)

class UpdateCategoryResponse(LanraragiResponse):
    category_id: str = Field(...)

class DeleteCategoryRequest(LanraragiRequest):
    id: str = Field(...)

class GetBookmarkLinkResponse(LanraragiResponse):
    category_id: str = Field(...)

class UpdateBookmarkLinkRequest(LanraragiRequest):
    id: str = Field(...)

class UpdateBookmarkLinkResponse(LanraragiResponse):
    category_id: str = Field(...)

class DisableBookmarkLinkResponse(LanraragiResponse):
    category_id: str = Field(...)

class AddArchiveToCategoryRequest(LanraragiRequest):
    id: str = Field(...)
    archive: str = Field(..., min_length=40, max_length=40)

class AddArchiveToCategoryResponse(LanraragiResponse):
    success_message: str = Field(...)

class RemoveArchiveFromCategoryRequest(LanraragiRequest):
    id: str = Field(...)
    archive: str = Field(..., min_length=40, max_length=40)
