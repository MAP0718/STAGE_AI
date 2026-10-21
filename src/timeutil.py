
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def parse(s):
    """'2026-10-01T06:30:00.000Z' -> datetime (timezone aware)."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(dt):
    """datetime -> '2026-10-01T06:30:00.000Z'."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ist_day(dt):
    return dt.astimezone(IST).date()


def is_quiet(dt):
    """True between 22:00 and 08:00 IST."""
    hour = dt.astimezone(IST).hour
    return hour >= 22 or hour < 8


def next_allowed(dt):
    """If dt is in quiet hours, move it to the next 08:00 IST. Otherwise keep it."""
    if not is_quiet(dt):
        return dt
    local = dt.astimezone(IST)
    if local.hour >= 22:
        local = local + timedelta(days=1)
    return local.replace(hour=8, minute=0, second=0, microsecond=0).astimezone(UTC)
