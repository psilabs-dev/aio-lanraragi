from pydantic import TypeAdapter

from lanraragi.models.database import (
    GetDatabaseBackupResponse,
    GetDatabaseStatsResponse,
    GetDatabaseStatsResponseTag,
)

_database_stats_adapter = TypeAdapter(list[GetDatabaseStatsResponseTag])

def _process_get_database_stats_response(content: str) -> GetDatabaseStatsResponse:
    return GetDatabaseStatsResponse(data=_database_stats_adapter.validate_json(content))

def _process_get_database_backup_response(content: str) -> GetDatabaseBackupResponse:
    return GetDatabaseBackupResponse.model_validate_json(content)

__all__ = [
    "_process_get_database_stats_response",
    "_process_get_database_backup_response"
]
