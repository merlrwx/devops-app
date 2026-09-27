import math
from datetime import UTC, datetime


def remaining_seconds(timer: dict) -> int:
    if timer["status"] == "running" and timer["ends_at"]:
        ends_at = datetime.fromisoformat(timer["ends_at"])
        return max(0, math.ceil((ends_at - datetime.now(UTC)).total_seconds()))
    return timer["remaining_seconds"]


def format_time(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02}:{seconds:02}"
