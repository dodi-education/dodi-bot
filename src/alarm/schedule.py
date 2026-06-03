"""Alarm schedule logic — pure functions, no I/O, unit-testable.

The alarm config (settings.json `alarm` block) is::

    {
      "stop_alarm_touch": true,
      "one_time_alarm": {"active": bool, "time": "HH:MM", "audio": "x.mp3"},
      "schedule": { "Mon": {"active": bool, "time": "HH:MM", "audio": "x.mp3"}, ... }
    }

A one-time alarm OVERRIDES the weekly schedule for the very next ring, then
deactivates (handled by the manager). This mirrors the design's `nextRing()`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # weekday() index order
DAY_FULL = {
    "Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
    "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday",
}


def _parse_t(t: str) -> tuple[int, int]:
    h, m = (t or "06:00").split(":")
    return int(h), int(m)


def next_ring(alarm_cfg: dict, now: datetime) -> Optional[dict]:
    """Return the next upcoming alarm, or None if nothing is active.

    Result: {"time": "HH:MM", "label": "Today"|"Tomorrow"|"Monday"|...,
             "day": "Mon"|None, "audio": str|None, "once": bool}
    While a one-time alarm is active it IS the next ring (the schedule is
    suppressed until it fires) — matching the design.
    """
    if not alarm_cfg:
        return None
    cur = now.hour * 60 + now.minute

    one = alarm_cfg.get("one_time_alarm") or {}
    if one.get("active"):
        h, m = _parse_t(one.get("time"))
        return {
            "time": one.get("time", "06:00"),
            "label": "Today" if (h * 60 + m) > cur else "Tomorrow",
            "day": None,
            "audio": one.get("audio"),
            "once": True,
        }

    schedule = alarm_cfg.get("schedule") or {}
    for i in range(8):
        d = now + timedelta(days=i)
        ab = DAYS[d.weekday()]
        s = schedule.get(ab)
        if s and s.get("active"):
            h, m = _parse_t(s.get("time"))
            if i == 0 and (h * 60 + m) <= cur:
                continue  # today's alarm time already passed
            label = "Today" if i == 0 else "Tomorrow" if i == 1 else DAY_FULL[ab]
            return {
                "time": s.get("time", "06:00"),
                "label": label,
                "day": ab,
                "audio": s.get("audio"),
                "once": False,
            }
    return None


def due_now(alarm_cfg: dict, now: datetime) -> Optional[dict]:
    """Return the alarm that should be ringing at `now` (this minute), or None.

    Result: {"time": "HH:MM", "audio": str|None, "once": bool, "day": str|None}.
    A one-time alarm overrides the schedule: while one is active, only it can
    fire (the schedule is suppressed until the one-time rings and deactivates).
    """
    if not alarm_cfg:
        return None
    hhmm = f"{now.hour:02d}:{now.minute:02d}"

    one = alarm_cfg.get("one_time_alarm") or {}
    if one.get("active"):
        if one.get("time") == hhmm:
            return {"time": hhmm, "audio": one.get("audio"), "once": True, "day": None}
        return None  # one-time pending -> schedule suppressed

    schedule = alarm_cfg.get("schedule") or {}
    ab = DAYS[now.weekday()]
    s = schedule.get(ab)
    if s and s.get("active") and s.get("time") == hhmm:
        return {"time": hhmm, "audio": s.get("audio"), "once": False, "day": ab}
    return None
