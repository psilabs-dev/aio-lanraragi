from pydantic import TypeAdapter

from lanraragi.models.stamp import (
    GetStampedPagesResponse,
    GetStampResponse,
    GetStampsByPageResponse,
    StampRecord,
)

_stamps_adapter = TypeAdapter(list[StampRecord])

def _process_get_stamp_response(content: str) -> GetStampResponse:
    return GetStampResponse.model_validate_json(content)

def _process_get_stamps_by_page_response(content: str) -> GetStampsByPageResponse:
    return GetStampsByPageResponse.model_validate_json(content)

def _process_get_stamped_pages_response(content: str) -> GetStampedPagesResponse:
    return GetStampedPagesResponse.model_validate_json(content)

__all__ = [
    "_process_get_stamp_response",
    "_process_get_stamps_by_page_response",
    "_process_get_stamped_pages_response",
]
