from typing import Optional
from pydantic import BaseModel, Field

class LanraragiRequest(BaseModel):
    max_retries: int = Field(default=0, description="Maximum number of retries to attempt if the request fails as a result of a transient error.")

class LanraragiResponse(BaseModel):
    message: Optional[str] = Field(None, description="Message returned by the server.")

class LanraragiErrorResponse(LanraragiResponse):
    error: str = Field(..., description="Error message returned by the server.")
    status: int = Field(..., description="Status code returned by the server.")
