import html
import json
import base64
import re

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

        # one common case is when a 500 server error occurs.
        # in this case, we need to parse the HTML first, then extract the inner error.
        content = content.strip()
        if content.startswith("<!DOCTYPE html>") and (match := re.search(r'<p>(.*?)</p>', content, re.DOTALL)):
            encoded = match.group(1).strip()
            decoded = html.unescape(encoded)
            ise_message = decoded
            err_message = f"Internal server error: {ise_message}"
        else:
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
