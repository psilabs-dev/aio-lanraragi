from pathlib import Path
from lanraragi.clients.utils import _build_auth_header, _build_err_response

def test_build_auth_header():
    assert _build_auth_header("lanraragi") == "Bearer bGFucmFyYWdp"

def test_build_err_response():
    with open(Path(__file__).parent / "resources" / "internal_server_error_response.html", 'r') as f:
        content = f.read()
    response = _build_err_response(content, 500)
    expected_error_msg = "Internal server error: Cannot open '/home/koyomi/lanraragi/thumb/62/62ec7b8c2493e8402cfa131d122384f3e734acdb.jpg' for writing: No such file or directory at /home/koyomi/lanraragi/script/../lib/LANraragi/Utils/Archive.pm line 55."
    assert response.error == expected_error_msg
