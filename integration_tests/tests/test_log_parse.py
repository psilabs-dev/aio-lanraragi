import datetime
from pathlib import Path

from aio_lanraragi_tests.log_parse import (
    MAX_REPORTED_ERROR_LOGS,
    LogEvent,
    format_error_logs,
    parse_lrr_logs,
)


def test_parse_lrr_logs():
    with open(Path(__file__).parent / "resources" / "lanraragi.log.test") as f:
        log_content = f.read()

    logs = parse_lrr_logs(log_content)
    assert len(logs) == 39

    assert logs[0].log_time == int(datetime.datetime.strptime("2025-10-31 06:26:38", "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.UTC).timestamp())
    assert logs[0].namespace == 'LANraragi'
    assert logs[0].severity_level == 'info'
    assert logs[0].message == 'LANraragi 0.9.50 started. (Production Mode)'
    assert str(logs[0]) == '[2025-10-31 06:26:38] [LANraragi] [info] LANraragi 0.9.50 started. (Production Mode)'

    assert logs[31].namespace == 'Metrics'
    assert logs[31].severity_level == 'info'
    assert logs[31].message == 'Cleaned up 101 metrics keys from previous session'

    assert logs[37].log_time == int(datetime.datetime.strptime("2025-11-04 20:48:52", "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.UTC).timestamp())
    assert logs[37].namespace == 'LANraragi'
    assert logs[37].severity_level == 'error'
    assert logs[37].message == 'Maketext error: [maketext doesn\'t know how to say:\nEnable Metrics\nas needed at /home/koyomi/lanraragi/script/../lib/LANraragi/Utils/I18NInitializer.pm line 48.\n]'


# Fixed timestamp used across format_error_logs tests so the expected output is
# deterministic. LogEvent.__str__ renders log_time in UTC.
ERROR_TS = "2025-11-04 20:48:52"
ERROR_EPOCH = int(datetime.datetime.strptime(ERROR_TS, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.UTC).timestamp())


def _error_event(message: str, namespace: str = "LANraragi") -> LogEvent:
    return LogEvent(log_time=ERROR_EPOCH, namespace=namespace, severity_level="error", message=message)


def test_format_error_logs_single():
    errors = [_error_event("Database connection failed")]
    assert format_error_logs("LANraragi", errors) == (
        "LANraragi process emitted 1 error log(s):\n"
        "[2025-11-04 20:48:52] [LANraragi] [error] Database connection failed"
    )


def test_format_error_logs_multiple_under_cap():
    # process_name is interpolated verbatim, and every event is listed in order.
    errors = [_error_event(f"boom {i}", namespace="Shinobu") for i in range(3)]
    assert format_error_logs("Shinobu", errors) == (
        "Shinobu process emitted 3 error log(s):\n"
        "[2025-11-04 20:48:52] [Shinobu] [error] boom 0\n"
        "[2025-11-04 20:48:52] [Shinobu] [error] boom 1\n"
        "[2025-11-04 20:48:52] [Shinobu] [error] boom 2"
    )


def test_format_error_logs_at_cap_has_no_overflow():
    errors = [_error_event(f"boom {i}") for i in range(MAX_REPORTED_ERROR_LOGS)]
    result = format_error_logs("LANraragi", errors)
    assert result.startswith(f"LANraragi process emitted {MAX_REPORTED_ERROR_LOGS} error log(s):\n")
    assert result.count("[error]") == MAX_REPORTED_ERROR_LOGS
    assert "... and" not in result


def test_format_error_logs_over_cap_truncates_with_overflow():
    errors = [_error_event(f"boom {i}") for i in range(MAX_REPORTED_ERROR_LOGS + 2)]
    result = format_error_logs("LANraragi", errors)
    # Header reports the total count, not the number displayed.
    assert result.startswith(f"LANraragi process emitted {MAX_REPORTED_ERROR_LOGS + 2} error log(s):\n")
    # Only the first MAX_REPORTED_ERROR_LOGS events are shown; the rest are summarized.
    assert result.count("[error]") == MAX_REPORTED_ERROR_LOGS
    assert "[error] boom 9" in result
    assert "[error] boom 10" not in result
    assert result.endswith("... and 2 more.")


def test_format_error_logs_preserves_multiline_message():
    # maketext errors span multiple lines; the whole message is kept intact.
    message = "Maketext error: [maketext doesn't know how to say:\nEnable Metrics\n]"
    errors = [_error_event(message)]
    assert format_error_logs("LANraragi", errors) == (
        "LANraragi process emitted 1 error log(s):\n"
        f"[2025-11-04 20:48:52] [LANraragi] [error] {message}"
    )
