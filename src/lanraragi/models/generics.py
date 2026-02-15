"""
Generic model types for API responses.
"""
from typing import TypeVar

from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse

__T = TypeVar('T', bound=LanraragiResponse)
_LRRClientResponse = tuple[__T | None, LanraragiErrorResponse | None]

__all__ = [
    "_LRRClientResponse"
]
