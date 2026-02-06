from typing import Any, Optional
from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse


class GetMinionJobStatusRequest(LanraragiRequest):
    job_id: int = Field(..., description="ID of the job.")

class GetMinionJobStatusResponse(LanraragiResponse):
    state: str = Field(...)
    task: str = Field(...)
    error: Optional[str] = Field(None)
    notes: Optional[dict[str, str]] = Field(None)

class GetMinionJobDetailRequest(LanraragiRequest):
    job_id: int = Field(..., description="ID of the job.")

class GetMinionJobDetailResponseResult(BaseModel):
    success: Optional[bool] = Field(None)
    id: Optional[str] = Field(None)
    message: Optional[str] = Field(None)
    url: Optional[str] = Field(None)
    category: Optional[str] = Field(None)
    title: Optional[str] = Field(None)
    error: Optional[str] = Field(None)
    data: Optional[dict[str, Any]] = Field(None)
    errors: Optional[list[str]] = Field(None)

class GetMinionJobDetailResponse(LanraragiResponse):
    id: int = Field(...)
    args: list[Any] = Field(default_factory=list)
    attempts: int = Field(0)
    children: list[int] = Field(default_factory=list)
    created: str = Field(...)
    delayed: str = Field(...)
    expires: Optional[str] = Field(None)
    finished: Optional[str] = Field(None)
    lax: Optional[int] = Field(None)
    notes: dict[str, Any] = Field(default_factory=dict)
    parents: list[int] = Field(default_factory=list)
    priority: int = Field(0)
    queue: str = Field("default")
    result: Optional[GetMinionJobDetailResponseResult] = Field(None)
    retried: Optional[str] = Field(None)
    retries: int = Field(0)
    started: Optional[str] = Field(None)
    state: str = Field(...)
    task: str = Field(...)
    time: Optional[str] = Field(None)
    worker: int = Field(default=0)

__all__ = [
    "GetMinionJobStatusRequest",
    "GetMinionJobStatusResponse",
    "GetMinionJobDetailRequest",
    "GetMinionJobDetailResponseResult",
    "GetMinionJobDetailResponse",
]
