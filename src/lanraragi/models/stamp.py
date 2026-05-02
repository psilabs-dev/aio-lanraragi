from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse


class StampRecord(BaseModel):
    id: str = Field(...)
    position: str = Field(...)
    content: str = Field(...)

class GetStampRequest(LanraragiRequest):
    stamp_id: str = Field(...)

class GetStampResponse(LanraragiResponse):
    result: StampRecord = Field(...)

class GetStampsByPageRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    index: int = Field(...)

class GetStampsByPageResponse(LanraragiResponse):
    result: list[StampRecord] = Field(...)

class GetStampedPagesRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class GetStampedPagesResponse(LanraragiResponse):
    result: list[str] = Field(...)

class AddStampRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    index: int = Field(...)
    content: str | None = Field(None)
    position: str | None = Field(None)

class AddStampResponse(LanraragiResponse):
    stamp_id: str = Field(...)

class UpdateStampRequest(LanraragiRequest):
    stamp_id: str = Field(...)
    content: str | None = Field(None)
    position: str | None = Field(None)

class UpdateStampResponse(LanraragiResponse):
    success_message: str | None = Field(None)

class DeleteStampRequest(LanraragiRequest):
    stamp_id: str = Field(...)

class DeleteStampResponse(LanraragiResponse):
    success_message: str | None = Field(None)

__all__ = [
    "StampRecord",
    "GetStampRequest",
    "GetStampResponse",
    "GetStampsByPageRequest",
    "GetStampsByPageResponse",
    "GetStampedPagesRequest",
    "GetStampedPagesResponse",
    "AddStampRequest",
    "AddStampResponse",
    "UpdateStampRequest",
    "UpdateStampResponse",
    "DeleteStampRequest",
    "DeleteStampResponse",
]
