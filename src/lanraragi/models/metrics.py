from pydantic import Field

from lanraragi.models.base import LanraragiResponse


class GetMetricsResponse(LanraragiResponse):
    content: str = Field(..., description="Raw Prometheus exposition format text.")

__all__ = [
    "GetMetricsResponse",
]
