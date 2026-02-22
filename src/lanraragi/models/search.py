from pydantic import BaseModel, Field, field_validator

from lanraragi.models.base import LanraragiRequest, LanraragiResponse


class SearchArchiveIndexRequest(LanraragiRequest):
    category: str | None = Field(None)
    search_filter: str | None = Field(None)
    start: str | None = Field(None)
    sortby: str | None = Field(None)
    order: str | None = Field(None)
    newonly: bool | None = Field(None)
    untaggedonly: bool | None = Field(None)
    groupby_tanks: bool = Field("true")

class SearchArchiveIndexResponseRecord(BaseModel):
    arcid: str = Field(...)  # 40-char SHA1 for archives, TANK_<timestamp> for tankoubons
    isnew: bool = Field(...)
    extension: str = Field(...)
    tags: str | None = Field(None)
    lastreadtime: int | None = Field(None)
    pagecount: int | None = Field(None)
    progress: int | None = Field(None)
    title: str = Field(...)

    @field_validator("arcid")
    @classmethod
    def validate_arcid_length(cls, v: str) -> str:
        if len(v) not in (15, 40):
            raise ValueError("arcid must be exactly 15 (tankoubon) or 40 (archive SHA1) characters")
        return v

class SearchArchiveIndexResponse(LanraragiResponse):
    data: list[SearchArchiveIndexResponseRecord] = Field(...)
    records_filtered: int = Field(..., validation_alias="recordsFiltered")
    records_total: int = Field(..., validation_alias="recordsTotal")

class GetRandomArchivesRequest(LanraragiRequest):
    category: str | None = Field(None)
    filter: str | None = Field(None)
    count: int = Field(5)
    newonly: bool | None = Field(None)
    untaggedonly: bool | None = Field(None)
    groupby_tanks: bool = Field("true")

class GetRandomArchivesResponse(LanraragiResponse):
    data: list[SearchArchiveIndexResponseRecord] = Field(...)
    records_total: int = Field(..., validation_alias="recordsTotal")

# <<<<< SEARCH <<<<<

__all__ = [
    "SearchArchiveIndexRequest",
    "SearchArchiveIndexResponseRecord",
    "SearchArchiveIndexResponse",
    "GetRandomArchivesRequest",
    "GetRandomArchivesResponse",
]
