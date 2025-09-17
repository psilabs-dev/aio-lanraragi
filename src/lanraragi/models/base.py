from typing import Optional
from pydantic import BaseModel, Field

class LanraragiRequest(BaseModel):
    """
    Base model for all LANraragi API client requests.
    """
    max_retries: int = Field(default=0, description="Maximum number of retries to attempt if the request fails as a result of a transient error.")

class LanraragiResponse(BaseModel):
    """
    Base model for all LANraragi API client non-error responses.
    """
    message: Optional[str] = Field(None, description="Message returned by the server.")

class LanraragiErrorResponse(LanraragiResponse):
    """
    Base model for all LANraragi API client error responses.
    """
    error: str = Field(..., description="Error message returned by the server.")
    status: int = Field(..., description="Status code returned by the server.")

__all__ = [
    "LanraragiRequest",
    "LanraragiResponse",
    "LanraragiErrorResponse",
]