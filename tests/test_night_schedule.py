"""Tests for night_mode.schedule — parsing and merging logic (display-only)."""

import sys
from datetime import datetime
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from night_mode.schedule import (  # noqa: E402
    Block,
    expand,
    merge,
    next_session_for,
    parse_timespan,
    week_monday,
)


MONDAY = datetime(2026, 6, 1)  # June 1, 2026 is a Monday


def test_parse_timespan_simple():
    s, e = parse_timespan("22:00-24:00", MONDAY)
    assert s == datetime(2026, 6, 1, 22, 0)
    assert e == datetime(2026, 6, 2, 0, 0)


def test_parse_timespan_legacy_wrap():
    # If end <= start, end is treated as next day.
    s, e = parse_timespan("22:00-06:00", MONDAY)
    assert s == datetime(2026, 6, 1, 22, 0)
    assert e == datetime(2026, 6, 2, 6, 0)


def test_week_monday():
    # A Wednesday should resolve to that week's Monday at 00:00.
    wed = datetime(2026, 6, 3, 14, 30)
    assert week_monday(wed) == datetime(2026, 6, 1, 0, 0)


def test_next_session_for_merges_sunday_evening_into_monday_morning():
    """The cross-week case: Su 22:00-24:00 + Mo 00:00-06:00 must be one session.

    `expand` only produces one week of blocks, so this merge only resolves when
    multiple weeks are expanded together — which is what `next_session_for` does.
    """
    schedule = {
        "Sun": [{"timespan": "22:00-24:00"}],
        "Mon": [{"timespan": "00:00-06:00"}],
    }
    now = datetime(2026, 6, 7, 23, 30)  # Sunday evening
    s = next_session_for(now, schedule)
    assert s is not None
    assert s.start_dt == datetime(2026, 6, 7, 22, 0)
    assert s.end_dt == datetime(2026, 6, 8, 6, 0)


def test_merge_overlapping_same_day():
    schedule = {
        "Mon": [
            {"timespan": "22:00-24:00"},
            {"timespan": "23:30-24:00"},
        ]
    }
    merged = merge(expand(schedule, MONDAY))
    assert len(merged) == 1
    assert merged[0].start_dt == datetime(2026, 6, 1, 22, 0)
    assert merged[0].end_dt == datetime(2026, 6, 2, 0, 0)


def test_merge_non_contiguous_stays_split():
    schedule = {
        "Mon": [
            {"timespan": "08:00-09:00"},
            {"timespan": "22:00-24:00"},
        ]
    }
    merged = merge(expand(schedule, MONDAY))
    assert len(merged) == 2


def test_next_session_for_inside_session_returns_containing():
    schedule = {
        "Sun": [{"timespan": "22:00-24:00"}],
        "Mon": [{"timespan": "00:00-06:00"}],
    }
    # Monday at 03:00 — inside the merged Su 22:00 → Mo 06:00 session.
    now = datetime(2026, 6, 1, 3, 0)
    s = next_session_for(now, schedule)
    assert s is not None
    assert s.start_dt <= now < s.end_dt


def test_next_session_for_returns_earliest_future():
    schedule = {"Mon": [{"timespan": "22:00-24:00"}]}
    # Monday at 10:00 — before the 22:00 block today.
    now = datetime(2026, 6, 1, 10, 0)
    s = next_session_for(now, schedule)
    assert s is not None
    assert s.start_dt == datetime(2026, 6, 1, 22, 0)


def test_next_session_for_empty_schedule():
    assert next_session_for(datetime(2026, 6, 1, 10, 0), {}) is None


def test_empty_day_arrays_ignored():
    schedule = {"Mon": [], "Tue": None, "Wed": [{"timespan": "10:00-11:00"}]}
    merged = merge(expand(schedule, MONDAY))
    assert len(merged) == 1
    assert merged[0].start_dt == datetime(2026, 6, 3, 10, 0)


def test_single_block():
    schedule = {"Mon": [{"timespan": "06:00-07:00"}]}
    merged = merge(expand(schedule, MONDAY))
    assert len(merged) == 1
    assert merged[0].start_dt == datetime(2026, 6, 1, 6, 0)
    assert merged[0].end_dt == datetime(2026, 6, 1, 7, 0)


def test_next_session_for_just_after_end_returns_next():
    schedule = {
        "Sun": [{"timespan": "22:00-24:00"}],
        "Mon": [{"timespan": "00:00-06:00"}],
        "Tue": [{"timespan": "22:00-24:00"}],
    }
    # Mo 06:00 — exactly the end. Should return Tuesday's 22:00 block.
    now = datetime(2026, 6, 1, 6, 0)
    s = next_session_for(now, schedule)
    assert s is not None
    assert s.start_dt == datetime(2026, 6, 2, 22, 0)
