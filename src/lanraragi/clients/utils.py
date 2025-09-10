import json
import base64

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

def _build_auth_header(lrr_api_key: str) -> str:
    """
    Converts a string key to 'Bearer <base64(key)>' format.
    """
    bearer = base64.b64encode(lrr_api_key.encode(encoding='utf-8')).decode('utf-8')
    return f"Bearer {bearer}"

__all__ = [
    "_build_auth_header",
    "_build_err_response"
]
