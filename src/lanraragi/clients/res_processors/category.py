from pydantic import TypeAdapter

from lanraragi.models.category import (
    GetAllCategoriesResponse,
    GetAllCategoriesResponseRecord,
    GetCategoryResponse
)

_all_categories_adapter = TypeAdapter(list[GetAllCategoriesResponseRecord])

def _process_get_all_categories_response(content: str) -> GetAllCategoriesResponse:
    return GetAllCategoriesResponse(data=_all_categories_adapter.validate_json(content))

def _process_get_category_response(content: str) -> GetCategoryResponse:
    return GetCategoryResponse.model_validate_json(content)

__all__ = [
    "_process_get_all_categories_response",
    "_process_get_category_response"
]
