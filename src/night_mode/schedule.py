"""Night mode schedule parsing and merging (display-only).

Pure functions only — no threads, no I/O. Unit-testable in isolation.

The schedule dict uses English day-abbreviation keys ("Mon", "Tue", "Wed",
"Thu", "Fri", "Sat", "Sun"). Each value is a list of `{timespan}` entries where
`timespan` is "HH:MM-HH:MM" in 24-hour local time. "24:00" is treated as
end-of-day (i.e. the next day at 00:00).

Night mode is now purely a DISPLAY scheduler (clock/blank during these
timespans); alarms live in the separate `alarm` config / `src/alarm` engine.

Two blocks are considered part of the same night-mode session when one's end
matches the other's start exactly (contiguous) or they overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional


DAY_KEYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class Block:
    start_dt: datetime
    end_dt: datetime


def parse_timespan(timespan: str, day0: datetime) -> tuple[datetime, datetime]:
    """Parse "HH:MM-HH:MM" into absolute [start, end) datetimes anchored on day0.

    `day0` must be midnight of the day the block belongs to. "24:00" maps to
    day0 + 1 day at 00:00. A start time strictly greater than the end time
    (legacy wrap form like "22:00-06:00") also rolls the end into the next day.
    """
    s, e = timespan.split("-")
    start = _parse_hhmm_on(day0, s)
    end = _parse_hhmm_on(day0, e)
    if end <= start:
        end = end + timedelta(days=1)
    return start, end


def _parse_hhmm_on(day0: datetime, hhmm: str) -> datetime:
    hh, mm = hhmm.split(":")
    h, m = int(hh), int(mm)
    if h == 24 and m == 0:
        return day0 + timedelta(days=1)
    return datetime.combine(day0.date(), time(hour=h, minute=m))


def expand(schedule_dict: dict, week_monday_midnight: datetime) -> list[Block]:
    """Expand one week of schedule entries into absolute Block intervals."""
    blocks: list[Block] = []
    for i, key in enumerate(DAY_KEYS):
        day0 = week_monday_midnight + timedelta(days=i)
        for entry in schedule_dict.get(key, []) or []:
            timespan = entry.get("timespan")
            if not timespan:
                continue
            start, end = parse_timespan(timespan, day0)
            blocks.append(Block(start_dt=start, end_dt=end))
    return blocks


def merge(blocks: list[Block]) -> list[Block]:
    """Fold contiguous/overlapping blocks into single display sessions."""
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda b: b.start_dt)
    out: list[Block] = [ordered[0]]
    for b in ordered[1:]:
        prev = out[-1]
        if b.start_dt <= prev.end_dt:
            out[-1] = Block(
                start_dt=prev.start_dt,
                end_dt=max(prev.end_dt, b.end_dt),
            )
        else:
            out.append(b)
    return out


def week_monday(now: datetime) -> datetime:
    """Return midnight of the Monday of `now`'s week (local time)."""
    monday = now - timedelta(days=now.weekday())
    return datetime.combine(monday.date(), time(0, 0))


def next_session_for(
    now: datetime, schedule_dict: dict
) -> Optional[Block]:
    """Return the merged session containing `now`, or the earliest future one.

    Expands three weeks (prev / current / next) so that sessions crossing
    midnight at week boundaries are found regardless of where `now` falls.
    Returns None if no future session exists in the next 7 days.
    """
    base = week_monday(now)
    all_blocks: list[Block] = []
    for offset_weeks in (-1, 0, 1):
        all_blocks.extend(
            expand(schedule_dict, base + timedelta(weeks=offset_weeks))
        )
    merged = merge(all_blocks)

    # First: any session that contains `now`.
    for b in merged:
        if b.start_dt <= now < b.end_dt:
            return b

    # Otherwise: earliest session strictly in the future.
    future = [b for b in merged if b.start_dt > now]
    if not future:
        return None
    return min(future, key=lambda b: b.start_dt)
