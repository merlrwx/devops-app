from datetime import UTC, datetime, timedelta

from timer_utils import format_time, remaining_seconds


def test_format_time_uses_minutes_and_seconds():
    assert format_time(0) == "00:00"
    assert format_time(65) == "01:05"
    assert format_time(3600) == "60:00"


def test_remaining_seconds_uses_stored_value_when_not_running():
    timer = {"status": "paused", "ends_at": None, "remaining_seconds": 42}

    assert remaining_seconds(timer) == 42


def test_remaining_seconds_never_goes_below_zero():
    timer = {
        "status": "running",
        "ends_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
        "remaining_seconds": 0,
    }

    assert remaining_seconds(timer) == 0
