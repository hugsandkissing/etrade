"""US equity regular-session time gate with daylight-saving awareness."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)


def is_regular_session(timestamp: datetime) -> bool:
    eastern = timestamp.astimezone(EASTERN)
    local_time = eastern.time().replace(tzinfo=None)
    return eastern.weekday() < 5 and OPEN <= local_time < CLOSE
