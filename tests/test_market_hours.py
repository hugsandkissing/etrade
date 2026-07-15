from datetime import datetime, timezone

from swagger.market_hours import is_regular_session


def test_regular_session_handles_daylight_saving_time():
    assert is_regular_session(datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc))
    assert is_regular_session(datetime(2026, 1, 14, 15, 0, tzinfo=timezone.utc))


def test_premarket_after_hours_and_weekend_are_rejected():
    assert not is_regular_session(datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc))
    assert not is_regular_session(datetime(2026, 7, 14, 21, 0, tzinfo=timezone.utc))
    assert not is_regular_session(datetime(2026, 7, 18, 15, 0, tzinfo=timezone.utc))
