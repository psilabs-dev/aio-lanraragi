import datetime
from pathlib import Path

from aio_lanraragi_tests.log_parse import parse_lrr_logs

def test_parse_lrr_logs():
    with open(Path(__file__).parent / "resources" / "lanraragi.log.test", 'r') as f:
        log_content = f.read()

    logs = parse_lrr_logs(log_content)
    assert len(logs) == 39

    assert logs[0].log_time == int(datetime.datetime.strptime("2025-10-31 06:26:38", "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp())
    assert logs[0].namespace == 'LANraragi'
    assert logs[0].severity_level == 'info'
    assert logs[0].message == 'LANraragi 0.9.50 started. (Production Mode)'
    assert str(logs[0]) == '[2025-10-31 06:26:38] [LANraragi] [info] LANraragi 0.9.50 started. (Production Mode)'

    assert logs[31].namespace == 'Metrics'
    assert logs[31].severity_level == 'info'
    assert logs[31].message == 'Cleaned up 101 metrics keys from previous session'

    assert logs[37].log_time == int(datetime.datetime.strptime("2025-11-04 20:48:52", "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp())
    assert logs[37].namespace == 'LANraragi'
    assert logs[37].severity_level == 'error'
    assert logs[37].message == 'Maketext error: [maketext doesn\'t know how to say:\nEnable Metrics\nas needed at /home/koyomi/lanraragi/script/../lib/LANraragi/Utils/I18NInitializer.pm line 48.\n]'
