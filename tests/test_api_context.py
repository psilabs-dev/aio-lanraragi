from lanraragi.clients.api_context import ApiContextManager
from lanraragi.clients.utils import _build_auth_header


def test_changed_api_key():

    api_context_manager = ApiContextManager("http://127.0.0.1:3000", "lanraragi")
    assert api_context_manager.headers["Authorization"] == _build_auth_header("lanraragi")
    api_context_manager.update_api_key(None)
    assert "Authorization" not in api_context_manager.headers
