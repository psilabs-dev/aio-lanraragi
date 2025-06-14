import json
from lanraragi.models.base import LanraragiErrorResponse

def _build_err_response(content: str, status: int) -> LanraragiErrorResponse:
    try:
        response_j = json.loads(content)
        response = LanraragiErrorResponse(error=response_j.get("error"), status=status)
        return response
    except json.decoder.JSONDecodeError:
        err_message = f"Error while decoding JSON from response: {content}"
        response = LanraragiErrorResponse(error=err_message, status=status)
        return response

__all__ = [
    "_build_err_response"
]
