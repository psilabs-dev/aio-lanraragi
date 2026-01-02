from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from lanraragi.models.base import LanraragiRequest, LanraragiResponse

class SearchArchiveIndexRequest(LanraragiRequest):
    category: Optional[str] = Field(None)
    search_filter: Optional[str] = Field(None)
    start: Optional[str] = Field(None)
    sortby: Optional[str] = Field(None)
    order: Optional[str] = Field(None)
    newonly: Optional[bool] = Field(None)
    untaggedonly: Optional[bool] = Field(None)
    groupby_tanks: bool = Field("true")

class SearchArchiveIndexResponseRecord(BaseModel):
    arcid: str = Field(...)  # 40-char SHA1 for archives, TANK_<timestamp> for tankoubons
    isnew: bool = Field(...)
    extension: str = Field(...)
    tags: Optional[str] = Field(None)
    lastreadtime: Optional[int] = Field(None)
    pagecount: Optional[int] = Field(None)
    progress: Optional[int] = Field(None)
    title: str = Field(...)

    @field_validator("arcid")
    @classmethod
    def validate_arcid_length(cls, v: str) -> str:
        if len(v) not in (15, 40):
            raise ValueError("arcid must be exactly 15 (tankoubon) or 40 (archive SHA1) characters")
        return v

class SearchArchiveIndexResponse(LanraragiResponse):
    data: List[SearchArchiveIndexResponseRecord] = Field(...)
    records_filtered: int = Field(...)
    records_total: int = Field(...)

class GetRandomArchivesRequest(LanraragiRequest):
    category: Optional[str] = Field(None)
    filter: Optional[str] = Field(None)
    count: int = Field(5)
    newonly: Optional[bool] = Field(None)
    untaggedonly: Optional[bool] = Field(None)
    groupby_tanks: bool = Field("true")

class GetRandomArchivesResponse(LanraragiResponse):
    data: List[SearchArchiveIndexResponseRecord] = Field(...)
    records_total: int = Field(...)

# <<<<< SEARCH <<<<<

__all__ = [
    "SearchArchiveIndexRequest",
    "SearchArchiveIndexResponseRecord",
    "SearchArchiveIndexResponse",
    "GetRandomArchivesRequest",
    "GetRandomArchivesResponse",
]