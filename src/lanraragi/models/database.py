from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse


class GetDatabaseStatsRequest(LanraragiRequest):
    minweight: int = Field(1)

class GetDatabaseStatsResponseTag(BaseModel):
    namespace: str = Field(...)
    text: str = Field(...)
    weight: int = Field(...)

class GetDatabaseStatsResponse(LanraragiResponse):
    data: list[GetDatabaseStatsResponseTag] = Field(...)

class CleanDatabaseResponse(LanraragiResponse):
    deleted: int = Field(...)
    unlinked: int = Field(...)

class GetDatabaseBackupArchiveRecord(BaseModel):
    arcid: str = Field(..., min_length=40, max_length=40)
    title: str | None = Field(None)
    tags: str | None = Field(None)
    summary: str | None = Field(None)
    thumbhash: str | None = Field(None)
    filename: str | None = Field(None)

class GetDatabaseBackupCategoryRecord(BaseModel):
    archives: list[str] = Field(...)
    category_id: str = Field(..., validation_alias="catid")
    name: str = Field(...)
    search: str = Field(...)

class GetDatabaseBackupTankoubonRecord(BaseModel):
    tankid: str = Field(...)
    name: str = Field(...)
    archives: list[str] = Field(...)

class GetDatabaseBackupResponse(LanraragiResponse):
    archives: list[GetDatabaseBackupArchiveRecord] = Field(default_factory=list)
    categories: list[GetDatabaseBackupCategoryRecord] = Field(default_factory=list)
    tankoubons: list[GetDatabaseBackupTankoubonRecord] = Field(default_factory=list)

__all__ = [
    "GetDatabaseStatsRequest",
    "GetDatabaseStatsResponseTag",
    "GetDatabaseStatsResponse",
    "CleanDatabaseResponse",
    "GetDatabaseBackupArchiveRecord",
    "GetDatabaseBackupCategoryRecord",
    "GetDatabaseBackupTankoubonRecord",
    "GetDatabaseBackupResponse",
]
