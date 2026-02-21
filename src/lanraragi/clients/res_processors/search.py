from lanraragi.models.search import (
    GetRandomArchivesResponse,
    SearchArchiveIndexResponse,
)


def _process_search_archive_index_response(content: str) -> SearchArchiveIndexResponse:
    return SearchArchiveIndexResponse.model_validate_json(content)

def _process_get_random_archives_response(content: str) -> GetRandomArchivesResponse:
    return GetRandomArchivesResponse.model_validate_json(content)

__all__ = [
    "_process_search_archive_index_response",
    "_process_get_random_archives_response"
]
