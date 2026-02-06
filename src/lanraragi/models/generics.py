"""
Generic model types for API responses.
"""
from typing import Optional, TypeVar

from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse

__T = TypeVar('T', bound=LanraragiResponse)
_LRRClientResponse = tuple[Optional[__T], Optional[LanraragiErrorResponse]]

__all__ = [
    "_LRRClientResponse"
]
