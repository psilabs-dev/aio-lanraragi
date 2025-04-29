"""
Generic model types for API responses.
"""
from typing import Optional, Tuple, TypeVar

from lanraragi.models.base import LanraragiErrorResponse, LanraragiResponse

T = TypeVar('T', bound=LanraragiResponse)
LRRClientResponse = Tuple[Optional[T], Optional[LanraragiErrorResponse]]