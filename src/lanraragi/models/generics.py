"""
Generic model types for API responses.
"""
from typing import Optional, Tuple, TypeVar

from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse

__T = TypeVar('T', bound=LanraragiResponse)
_LRRClientResponse = Tuple[Optional[__T], Optional[LanraragiErrorResponse]]

__all__ = [
    "_LRRClientResponse"
]
