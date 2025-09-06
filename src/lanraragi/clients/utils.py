import json

from lanraragi.models.base import LanraragiErrorResponse

def _build_err_response(content: str, status: int) -> LanraragiErrorResponse:
    try:
        response_j = json.loads(content)

        # ideally, LRR will return an error message in the usual format.
        # however, if e.g. openapi returns "errors" instead, we'll just dump the entire message.
        if "error" in response_j:
            response = LanraragiErrorResponse(error=str(response_j.get("error")), status=status)
            return response
        else:
            return LanraragiErrorResponse(error=str(response_j), status=status)
    except json.decoder.JSONDecodeError:
        err_message = f"Error while decoding JSON from response: {content}"
        response = LanraragiErrorResponse(error=err_message, status=status)
        return response

__all__ = [
    "_build_err_response"
]
