from typing import List, Optional
from pydantic import BaseModel, Field
from lanraragi.models.base import LanraragiRequest, LanraragiResponse

class GetDatabaseStatsRequest(LanraragiRequest):
    minweight: int = Field(1)

class GetDatabaseStatsResponseTag(BaseModel):
    namespace: str = Field(...)
    text: str = Field(...)
    weight: int = Field(...)

class GetDatabaseStatsResponse(LanraragiResponse):
    data: List[GetDatabaseStatsResponseTag] = Field(...)

class CleanDatabaseResponse(LanraragiResponse):
    deleted: int = Field(...)
    unlinked: int = Field(...)

class GetDatabaseBackupArchiveRecord(BaseModel):
    arcid: str = Field(..., min_length=40, max_length=40)
    title: Optional[str] = Field(None)
    tags: Optional[str] = Field(None)
    summary: Optional[str] = Field(None)
    thumbhash: Optional[str] = Field(None)
    filename: Optional[str] = Field(None)

class GetDatabaseBackupCategoryRecord(BaseModel):
    archives: List[str] = Field(...)
    category_id: str = Field(...)
    name: str = Field(...)
    search: str = Field(...)

class GetDatabaseBackupTankoubonRecord(BaseModel):
    tankid: str = Field(...)
    name: str = Field(...)
    archives: List[str] = Field(...)

class GetDatabaseBackupResponse(LanraragiResponse):
    archives: List[GetDatabaseBackupArchiveRecord] = Field(...)
    categories: List[GetDatabaseBackupCategoryRecord] = Field(...)
    tankoubons: List[GetDatabaseBackupTankoubonRecord] = Field(...)

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
