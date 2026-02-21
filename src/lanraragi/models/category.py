from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse


class GetAllCategoriesResponseRecord(BaseModel):
    archives: list[str] = Field(...)
    category_id: str = Field(..., validation_alias="id")
    name: str = Field(...)
    pinned: bool = Field(...)
    search: str = Field(...)

class GetAllCategoriesResponse(LanraragiResponse):
    data: list[GetAllCategoriesResponseRecord] = Field(...)

class GetCategoryRequest(LanraragiRequest):
    category_id: str = Field(...)

class GetCategoryResponse(LanraragiResponse):
    archives: list[str] = Field(...)
    category_id: str = Field(..., validation_alias="id")
    name: str = Field(...)
    pinned: bool = Field(...)
    search: str = Field(...)

class CreateCategoryRequest(LanraragiRequest):
    name: str = Field(...)
    pinned: bool | None = Field(None)
    search: str | None = Field(None)

class CreateCategoryResponse(LanraragiResponse):
    category_id: str = Field(...)

class UpdateCategoryRequest(LanraragiRequest):
    category_id: str = Field(...)
    pinned: bool | None = Field(None)
    name: str | None = Field(None)
    search: str | None = Field(None)

class UpdateCategoryResponse(LanraragiResponse):
    category_id: str = Field(...)

class DeleteCategoryRequest(LanraragiRequest):
    category_id: str = Field(...)

class GetBookmarkLinkResponse(LanraragiResponse):
    # may not be present if bookmark link is disabled
    category_id: str | None = Field(None)

class UpdateBookmarkLinkRequest(LanraragiRequest):
    category_id: str = Field(...)

class UpdateBookmarkLinkResponse(LanraragiResponse):
    category_id: str = Field(...)

class DisableBookmarkLinkResponse(LanraragiResponse):
    category_id: str = Field(...)

class AddArchiveToCategoryRequest(LanraragiRequest):
    category_id: str = Field(...)
    arcid: str = Field(..., min_length=40, max_length=40)

class AddArchiveToCategoryResponse(LanraragiResponse):
    success_message: str = Field(...)

class RemoveArchiveFromCategoryRequest(LanraragiRequest):
    category_id: str = Field(...)
    arcid: str = Field(..., min_length=40, max_length=40)

__all__ = [
    "GetAllCategoriesResponseRecord",
    "GetAllCategoriesResponse",
    "GetCategoryRequest",
    "GetCategoryResponse",
    "CreateCategoryRequest",
    "CreateCategoryResponse",
    "UpdateCategoryRequest",
    "UpdateCategoryResponse",
    "DeleteCategoryRequest",
    "GetBookmarkLinkResponse",
    "UpdateBookmarkLinkRequest",
    "UpdateBookmarkLinkResponse",
    "DisableBookmarkLinkResponse",
    "AddArchiveToCategoryRequest",
    "AddArchiveToCategoryResponse",
    "RemoveArchiveFromCategoryRequest",
]
