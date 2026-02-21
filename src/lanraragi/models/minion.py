from typing import Any

from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse


class GetMinionJobStatusRequest(LanraragiRequest):
    job_id: int = Field(..., description="ID of the job.")

class GetMinionJobStatusResponse(LanraragiResponse):
    state: str = Field(...)
    task: str = Field(...)
    error: str | None = Field(None)
    notes: dict[str, str] | None = Field(None)

class GetMinionJobDetailRequest(LanraragiRequest):
    job_id: int = Field(..., description="ID of the job.")

class GetMinionJobDetailResponseResult(BaseModel):
    success: bool | None = Field(None)
    id: str | None = Field(None)
    message: str | None = Field(None)
    url: str | None = Field(None)
    category: str | None = Field(None)
    title: str | None = Field(None)
    error: str | None = Field(None)
    data: dict[str, Any] | None = Field(None)
    errors: list[str] | None = Field(None)

class GetMinionJobDetailResponse(LanraragiResponse):
    id: int = Field(...)
    args: list[Any] = Field(default_factory=list)
    attempts: int = Field(0)
    children: list[int] = Field(default_factory=list)
    created: str = Field(...)
    delayed: str = Field(...)
    expires: str | None = Field(None)
    finished: str | None = Field(None)
    lax: int | None = Field(None)
    notes: dict[str, Any] = Field(default_factory=dict)
    parents: list[int] = Field(default_factory=list)
    priority: int = Field(0)
    queue: str = Field("default")
    result: GetMinionJobDetailResponseResult | None = Field(None)
    retried: str | None = Field(None)
    retries: int = Field(0)
    started: str | None = Field(None)
    state: str = Field(...)
    task: str = Field(...)
    time: str | None = Field(None)
    worker: int = Field(default=0)

__all__ = [
    "GetMinionJobStatusRequest",
    "GetMinionJobStatusResponse",
    "GetMinionJobDetailRequest",
    "GetMinionJobDetailResponseResult",
    "GetMinionJobDetailResponse",
]
