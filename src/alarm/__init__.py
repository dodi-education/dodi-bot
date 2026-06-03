"""Standalone alarm engine (decoupled from night mode).

Only the pure schedule helpers are re-exported here so that lightweight
importers (e.g. the display thread, for the clock's next-alarm line) don't pull
in the heavy manager (which imports display/night_mode and would create an
import cycle). Import the manager explicitly: ``from alarm.manager import AlarmManager``.
"""

from .schedule import due_now, next_ring

__all__ = ["due_now", "next_ring"]
