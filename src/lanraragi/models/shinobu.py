from pydantic import Field

from lanraragi.models.base import LanraragiResponse

class GetShinobuStatusResponse(LanraragiResponse):
    is_alive: bool = Field(...)
    pid: int = Field(...)

class RestartShinobuResponse(LanraragiResponse):
    new_pid: int = Field(...)

__all__ = [
    "GetShinobuStatusResponse",
    "RestartShinobuResponse",
]
