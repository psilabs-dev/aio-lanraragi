from pathlib import Path

from lanraragi.clients.utils import _build_auth_header, _parse_500_error_message


def test_build_auth_header():
    assert _build_auth_header("lanraragi") == "Bearer bGFucmFyYWdp"

def test_build_err_response():
    with open(Path(__file__).parent / "resources" / "internal_server_error_response.html") as f:
        content = f.read()
    response = _parse_500_error_message(content)
    expected_error_msg = "Cannot open '/home/koyomi/lanraragi/thumb/62/62ec7b8c2493e8402cfa131d122384f3e734acdb.jpg' for writing: No such file or directory at /home/koyomi/lanraragi/script/../lib/LANraragi/Utils/Archive.pm line 55."
    assert response == expected_error_msg
