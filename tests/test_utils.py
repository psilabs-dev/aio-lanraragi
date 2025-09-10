from lanraragi.clients.utils import _build_auth_header

def test_build_auth_header():
    assert _build_auth_header("lanraragi") == "Bearer bGFucmFyYWdp"
