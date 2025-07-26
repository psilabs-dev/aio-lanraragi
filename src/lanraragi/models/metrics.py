from typing import Iterable
from pydantic import Field, ConfigDict
from lanraragi.models.base import LanraragiResponse
from prometheus_client.metrics_core import Metric

class GetMetricsResponse(LanraragiResponse):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    metrics: Iterable[Metric] = Field(...)

__all__ = [
    "GetMetricsResponse"
]
