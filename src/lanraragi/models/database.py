from typing import List
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
