import json
from pydantic import TypeAdapter

from lanraragi.models.archive import (
    GetAllArchivesResponse,
    GetAllArchivesResponseRecord,
    GetArchiveCategoriesCatRecord,
    GetArchiveCategoriesResponse,
    GetArchiveMetadataResponse,
    GetArchiveThumbnailResponse
)

_all_archives_adapter = TypeAdapter(list[GetAllArchivesResponseRecord])
_archive_categories_adapter = TypeAdapter(list[GetArchiveCategoriesCatRecord])

def _process_get_all_archives_response(content: str) -> GetAllArchivesResponse:
    return GetAllArchivesResponse(data=_all_archives_adapter.validate_json(content))

def _process_get_archive_metadata_response(content: str) -> GetArchiveMetadataResponse:
    return GetArchiveMetadataResponse.model_validate_json(content)

def _process_get_archive_categories_response(content: str) -> GetArchiveCategoriesResponse:
    if content.lstrip().startswith("{"):
        return GetArchiveCategoriesResponse.model_validate_json(content)
    return GetArchiveCategoriesResponse(categories=_archive_categories_adapter.validate_json(content))

def _process_get_archive_thumbnail_response(content: str, status: int) -> GetArchiveThumbnailResponse:
    """
    Handle all successful status codes (200 or 202).
    """
    if status == 200:
        return GetArchiveThumbnailResponse(content=content, content_type="image/jpeg")
    elif status == 202:
        response_j = json.loads(content)
        job = response_j.get("job")
        return GetArchiveThumbnailResponse(job=job, content=None, content_type=None)

__all__ = [
    "_process_get_all_archives_response",
    "_process_get_archive_metadata_response",
    "_process_get_archive_categories_response",
    "_process_get_archive_thumbnail_response"
]
