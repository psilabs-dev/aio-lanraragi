import json
from typing import List
from lanraragi.models.category import GetAllCategoriesResponse, GetAllCategoriesResponseRecord, GetCategoryResponse


def _process_get_all_categories_response(content: str) -> GetAllCategoriesResponse:
    data = json.loads(content) # note: this is a list of categories.
    categories: List[GetAllCategoriesResponseRecord] = []
    for category in data:
        archives = category.get("archives")
        id = category.get("id")
        name = category.get("name")
        pinned = category.get("pinned") == "1"
        search = category.get("search")
        categories.append(GetAllCategoriesResponseRecord(
            archives=archives, category_id=id, name=name, pinned=pinned, search=search
        ))
    response = GetAllCategoriesResponse(
        data=categories
    )
    return response

def _process_get_category_response(content: str) -> GetCategoryResponse:
    response_j = json.loads(content)
    archives = response_j.get("archives")
    id = response_j.get("id")
    name = response_j.get("name")
    pinned = response_j.get("pinned") == "1"
    search = response_j.get("search")
    return GetCategoryResponse(archives=archives, category_id=id, name=name, pinned=pinned, search=search)

__all__ = [
    "_process_get_all_categories_response",
    "_process_get_category_response"
]