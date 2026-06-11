"""Alarm settings screen — touch UI rendered in the pygame thread.

Faithfully reproduces the Claude Design "Dodi-Bot Alarm" page for the round
800x800 screen: Weekly / One-time tabs, a day strip, a big hero time that opens
a circular drag-to-set dial, a sound picker sheet, an Active switch, and a close
button. Day/night theme auto-selected by hour; accent #3E84BE.

Edits mutate the live ``settings["alarm"]`` dict and persist via the bound save
callback (auto-activating an alarm when its time is Set).
"""

from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path

import pygame

from . import theme
from . import widgets

SIZE = 800
CX = SIZE // 2
_DIAL_CY = 416  # dial center Y (design alarm-dial.jsx: CY=416)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_FULL = {
    "Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
    "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday",
}
DAY_2 = {"Mon": "Mo", "Tue": "Tu", "Wed": "We", "Thu": "Th", "Fri": "Fr", "Sat": "Sa", "Sun": "Su"}

_AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "audio"
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}


def _today_abbr() -> str:
    return DAYS[_dt.datetime.now().weekday()]


def _parse_t(t: str):
    h, m = (t or "06:00").split(":")
    return int(h), int(m)


def _sound_label(a: str) -> str:
    base = (a or "").rsplit(".", 1)[0]
    return base[:1].upper() + base[1:] if base else "—"


def _audio_options(current: str) -> list:
    opts = []
    try:
        opts = sorted(p.name for p in _AUDIO_DIR.iterdir() if p.suffix.lower() in _AUDIO_EXTS)
    except Exception:
        opts = []
    if current and current not in opts:
        opts.insert(0, current)  # keep a configured-but-missing file visible
    return opts or ([current] if current else [])


class SettingsScreen:
    """Stateful alarm settings UI. Lives in (and is driven by) the avatar thread."""

    def __init__(self, avatar):
        self._avatar = avatar
        self.tab = "weekly"           # 'weekly' | 'once'
        self.selected = _today_abbr()
        self.overlay = None           # None | 'dial' | 'audio'
        self.theme_name = "day"
        self._bg = None
        self._rects: dict = {}
        self._has_touch = False
        # dial state
        self._dh, self._dm = 6, 0
        self._dfield = "h"
        self._dragging = False

    # ---------------------------------------------------------------- state

    @property
    def _alarm(self) -> dict:
        return self._avatar._alarm_cfg()

    def _entity(self) -> dict:
        a = self._alarm
        if self.tab == "weekly":
            return (a.get("schedule") or {}).get(self.selected) or {}
        return a.get("one_time_alarm") or {}

    def _patch(self, **kw) -> None:
        ent = self._entity()
        ent.update(kw)
        self._save()

    def _save(self) -> None:
        av = self._avatar
        if av._save_settings is not None and av._settings is not None:
            try:
                av._save_settings(av._settings)
            except Exception:
                pass

    def open(self) -> None:
        """(Re)initialise when the overlay is opened."""
        self.tab = "weekly"
        self.selected = _today_abbr()
        self.overlay = None
        self._dragging = False
        self.theme_name = self._avatar.theme_name()
        self._bg = None  # rebuild gradient for the (possibly changed) theme

    # ----------------------------------------------------------- next-ring

    def _next_ring_label(self):
        from alarm.schedule import next_ring
        nr = next_ring(self._alarm, _dt.datetime.now())
        if nr is None:
            return None
        prefix = "One-time · " if nr["once"] else ""
        return f"{prefix}{nr['label']} · {nr['time']}"

    # --------------------------------------------------------- event input

    def _xy(self, event):
        return widgets.event_xy(event, SIZE)

    def handle_event(self, event) -> None:
        t = event.type
        if t in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
            self._has_touch = True
        # On a touchscreen SDL also synthesizes mouse events — ignore those.
        is_mouse = t in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION)
        if is_mouse and self._has_touch:
            return

        down = t in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN)
        move = t in (pygame.FINGERMOTION,) or (t == pygame.MOUSEMOTION and any(pygame.mouse.get_pressed()))
        up = t in (pygame.FINGERUP, pygame.MOUSEBUTTONUP)

        xy = self._xy(event)
        if xy is None:
            return

        if self.overlay == "dial":
            self._dial_event(down, move, up, xy)
        elif self.overlay == "audio":
            if down:
                self._audio_event(xy)
        else:
            if down:
                self._main_event(xy)

    # ------------------------------------------------------------- main hit

    def _hit(self, name, xy) -> bool:
        r = self._rects.get(name)
        return bool(r and r.collidepoint(xy))

    def _main_event(self, xy) -> None:
        if self._hit("close", xy):
            self._avatar.close_settings()
            return
        if self._hit("tab_weekly", xy):
            self.tab = "weekly"
            return
        if self._hit("tab_once", xy):
            self.tab = "once"
            return
        if self.tab == "weekly":
            for d in DAYS:
                if self._hit(f"chip_{d}", xy):
                    self.selected = d
                    return
        if self._hit("hero", xy):
            h, m = _parse_t(self._entity().get("time", "06:00"))
            self._dh, self._dm, self._dfield = h, m, "h"
            self.overlay = "dial"
            return
        if self._hit("sound", xy):
            self.overlay = "audio"
            return
        if self._hit("active", xy):
            self._patch(active=not self._entity().get("active", False))
            return

    def _audio_event(self, xy) -> None:
        if self._hit("audio_scrim", xy):
            self.overlay = None
            return
        for name, val in self._rects.get("_audio_opts", []):
            if name.collidepoint(xy):
                self._patch(audio=val)
                self.overlay = None
                return

    # ------------------------------------------------------------- dial hit

    def _dial_event(self, down, move, up, xy) -> None:
        if down:
            if self._hit("dial_cancel", xy):
                self.overlay = None
                return
            if self._hit("dial_set", xy):
                self._commit_dial()
                return
            if self._hit("dial_seg_h", xy):
                self._dfield = "h"
                return
            if self._hit("dial_seg_m", xy):
                self._dfield = "m"
                return
            self._dragging = True
            self._dial_from_xy(xy)
        elif move and self._dragging:
            self._dial_from_xy(xy)
        elif up:
            self._dragging = False

    def _dial_from_xy(self, xy) -> None:
        # Geometry from the design's alarm-dial.jsx (C=400, CY=416).
        px, py = xy
        theta = math.atan2(px - CX, -(py - _DIAL_CY))
        if theta < 0:
            theta += 2 * math.pi
        frac = theta / (2 * math.pi)
        if self._dfield == "h":
            self._dh = round(frac * 24) % 24
        else:
            self._dm = round(frac * 60) % 60

    def _commit_dial(self) -> None:
        time_str = f"{self._dh:02d}:{self._dm:02d}"
        self._patch(time=time_str, active=True)  # Set auto-activates the alarm
        self.overlay = None

    # ---------------------------------------------------------------- draw

    def draw(self, screen) -> None:
        th = theme.THEMES[self.theme_name]
        if self._bg is None:
            self._bg = theme.make_gradient_bg(SIZE, SIZE, th["bg_top"], th["bg_bottom"])
        screen.blit(self._bg, (0, 0))
        self._rects = {}

        self._draw_pill(screen, th)
        self._draw_tabs(screen, th)
        if self.tab == "weekly":
            self._draw_week_strip(screen, th)
        else:
            self._draw_once_info(screen, th)
        self._draw_hero(screen, th)
        self._draw_actions(screen, th)
        self._draw_close(screen, th)

        if self.overlay == "dial":
            self._draw_dial(screen, th)
        elif self.overlay == "audio":
            self._draw_audio(screen, th)

    def _text(self, screen, s, size, weight, color, center=None, topleft=None, midleft=None):
        return widgets.text(screen, s, size, weight, color,
                            center=center, topleft=topleft, midleft=midleft)

    def _draw_pill(self, screen, th) -> None:
        label = self._next_ring_label()
        if not label:
            label = "No alarms set"
        else:
            pygame.draw.circle(screen, theme.ACCENT, (CX - self._text_w(label, 17, 700) // 2 - 14, 88), 5)
        self._text(screen, label, 17, 700, th["muted"], center=(CX, 88))

    def _text_w(self, s, size, weight) -> int:
        return widgets.text_w(s, size, weight)

    def _draw_tabs(self, screen, th) -> None:
        w, h, top = 312, 90, 110  # double-height toggle, nudged up to clear the chips
        outer = pygame.Rect(CX - w // 2, top, w, h)
        pygame.draw.rect(screen, th["surface2"], outer, border_radius=h // 2)
        half = (w - 12) // 2
        for i, (key, lbl) in enumerate([("weekly", "Weekly"), ("once", "One-time")]):
            r = pygame.Rect(outer.x + 6 + i * half, outer.y + 6, half, h - 12)
            on = self.tab == key
            if on:
                pygame.draw.rect(screen, theme.ACCENT, r, border_radius=(h - 12) // 2)
            self._text(screen, lbl, 18, 800, (255, 255, 255) if on else th["muted"], center=r.center)
            self._rects[f"tab_{key}"] = r

    def _draw_week_strip(self, screen, th) -> None:
        cw, ch, gap, top = 70, 88, 12, 226
        total = 7 * cw + 6 * gap
        x = CX - total // 2
        is24 = True
        for d in DAYS:
            s = (self._alarm.get("schedule") or {}).get(d) or {}
            active = bool(s.get("active"))
            sel = d == self.selected
            r = pygame.Rect(x, top, cw, ch)
            bg = theme.ACCENT if active else th["surface"]
            pygame.draw.rect(screen, bg, r, border_radius=22)
            border = theme.ACCENT if (active or sel) else th["ring"]
            pygame.draw.rect(screen, border, r, width=3 if sel else 0 if active else 3, border_radius=22)
            day_col = (255, 255, 255) if active else th["ink"]
            time_col = (255, 255, 255) if active else th["muted"]
            self._text(screen, DAY_2[d], 18, 800, day_col, center=(r.centerx, r.centery - 12))
            tlabel = s.get("time", "06:00") if active else "off"
            self._text(screen, tlabel, 14, 700, time_col, center=(r.centerx, r.centery + 16))
            self._rects[f"chip_{d}"] = r
            x += cw + gap

    def _draw_once_info(self, screen, th) -> None:
        w, top = 452, 222
        r = pygame.Rect(CX - w // 2, top, w, 96)
        pygame.draw.rect(screen, th["surface"], r, border_radius=26)
        self._text(screen, "Rings once, then Dodi returns to", 19, 700, th["ink"], center=(CX, top + 36))
        self._text(screen, "your weekly schedule automatically.", 19, 700, th["ink"], center=(CX, top + 62))

    def _draw_hero(self, screen, th) -> None:
        ent = self._entity()
        active = bool(ent.get("active"))
        label = DAY_FULL[self.selected] if self.tab == "weekly" else "One-time alarm"
        self._text(screen, label.upper(), 19, 700, th["muted"], center=(CX, 350))
        time_str = ent.get("time", "06:00")
        # Inactive alarm: dim the time toward the background (design opacity .38).
        color = th["ink"] if active else tuple(int(c * 0.38 + bgc * 0.62) for c, bgc in zip(th["ink"], th["bg_top"]))
        surf = theme.font(108, 900).render(time_str, True, color)
        rect = surf.get_rect(center=(CX, 420))
        screen.blit(surf, rect)
        pygame.draw.rect(screen, theme.ACCENT, pygame.Rect(CX - 33, rect.bottom + 6, 66, 6), border_radius=3)
        self._rects["hero"] = rect.inflate(60, 40)

    def _draw_actions(self, screen, th) -> None:
        cw, chh, gap, top = 230, 92, 18, 540  # lowered to give the hero underline room
        ent = self._entity()
        # Sound card (left)
        sr = pygame.Rect(CX - cw - gap // 2, top, cw, chh)
        pygame.draw.rect(screen, th["surface"], sr, border_radius=24)
        self._text(screen, "Sound", 17, 700, th["muted"], midleft=(sr.x + 22, sr.y + 28))
        self._text(screen, _sound_label(ent.get("audio", "")), 20, 800, th["ink"], midleft=(sr.x + 22, sr.y + 62))
        self._rects["sound"] = sr
        # Active card (right) with switch
        ar = pygame.Rect(CX + gap // 2, top, cw, chh)
        pygame.draw.rect(screen, th["surface"], ar, border_radius=24)
        self._text(screen, "Active", 17, 700, th["muted"], midleft=(ar.x + 22, ar.centery))
        self._draw_switch(screen, th, ar.right - 22 - 74, ar.centery - 21, bool(ent.get("active")))
        self._rects["active"] = ar

    def _draw_switch(self, screen, th, x, y, on) -> None:
        widgets.draw_switch(screen, th, x, y, on)

    def _draw_close(self, screen, th) -> None:
        self._rects["close"] = widgets.close_button(screen, th)

    # --------------------------------------------------------- dial overlay

    def _draw_dial(self, screen, th) -> None:
        self._scrim(screen)
        C, CYD, R, LR = CX, _DIAL_CY, 224, 224 - 38

        # editable HH : MM readout at the top (over the dimmed/blurred scrim, so
        # the inactive segments use a light colour; the active one gets a chip).
        ry = 110
        seg_w = 90
        hr = pygame.Rect(CX - seg_w - 18, ry - 36, seg_w, 72)
        mr = pygame.Rect(CX + 18, ry - 36, seg_w, 72)
        for r, s, field in [(hr, f"{self._dh:02d}", "h"), (mr, f"{self._dm:02d}", "m")]:
            on = self._dfield == field
            if on:
                pygame.draw.rect(screen, th["surface"], r, border_radius=14)
            self._text(screen, s, 58, 900, th["ink"] if on else (238, 246, 252), center=r.center)
        self._text(screen, ":", 54, 900, (238, 246, 252), center=(CX, ry))
        self._rects["dial_seg_h"] = hr
        self._rects["dial_seg_m"] = mr

        # dial face
        pygame.draw.circle(screen, th["surface"], (C, CYD), R)
        pygame.draw.circle(screen, th["ring"], (C, CYD), R, 2)

        is_h = self._dfield == "h"
        N = 24 if is_h else 60
        for i in range(N):
            a = i / N * 2 * math.pi
            major = (i % 3 == 0) if is_h else (i % 5 == 0)
            inr = R - (17 if major else 9)
            x1, y1 = C + inr * math.sin(a), CYD - inr * math.cos(a)
            x2, y2 = C + R * math.sin(a), CYD - R * math.cos(a)
            pygame.draw.line(screen, th["ink"] if major else th["muted"], (x1, y1), (x2, y2), 3 if major else 2)
            if major:
                lbl = (f"{i:02d}" if is_h else f"{i:02d}")
                lx, ly = C + LR * math.sin(a), CYD - LR * math.cos(a)
                self._text(screen, lbl, 22, 800, th["ink"], center=(lx, ly))

        # hand
        val = (self._dh if is_h else self._dm)
        frac = (val / 24.0) if is_h else (val / 60.0)
        theta = frac * 2 * math.pi
        hx, hy = C + R * math.sin(theta), CYD - R * math.cos(theta)
        pygame.draw.line(screen, theme.ACCENT, (C, CYD), (hx, hy), 6)
        pygame.draw.circle(screen, theme.ACCENT, (int(hx), int(hy)), 27)
        self._text(screen, f"{val:02d}", 23, 900, (255, 255, 255), center=(hx, hy))

        # center Set button
        pygame.draw.circle(screen, theme.ACCENT, (C, CYD), 54)
        self._text(screen, "Set", 27, 800, (255, 255, 255), center=(C, CYD))
        self._rects["dial_set"] = pygame.Rect(C - 54, CYD - 54, 108, 108)

        # cancel button (bottom) — identical to the main close button
        self._rects["dial_cancel"] = widgets.close_button(screen, th)

    # -------------------------------------------------------- audio overlay

    def _draw_audio(self, screen, th) -> None:
        self._scrim(screen)
        self._rects["audio_scrim"] = pygame.Rect(0, 0, SIZE, SIZE)
        cur = self._entity().get("audio", "")
        opts = _audio_options(cur)
        w = 452
        rows = len(opts)
        h = 24 + 38 + rows * 62 + 18
        top = max(120, (SIZE - h) // 2)
        panel = pygame.Rect(CX - w // 2, top, w, h)
        pygame.draw.rect(screen, th["surface"], panel, border_radius=30)
        self._text(screen, "Wake-up sound", 23, 900, th["ink"], center=(CX, top + 30))
        opt_rects = []
        y = top + 24 + 38
        for a in opts:
            r = pygame.Rect(panel.x + 20, y, w - 40, 62)
            on = a == cur
            if on:
                pygame.draw.rect(screen, th["surface2"], r, border_radius=16)
            pygame.draw.circle(screen, theme.ACCENT if on else th["surface"], (r.x + 35, r.centery), 19)
            pygame.draw.circle(screen, theme.ACCENT, (r.x + 35, r.centery), 19, 2)
            self._glyph_note(screen, r.x + 35, r.centery, 22, (255, 255, 255) if on else theme.ACCENT)
            self._text(screen, _sound_label(a), 20, 700, th["ink"], midleft=(r.x + 66, r.centery))
            if on:
                self._glyph_check(screen, r.right - 26, r.centery, 20, theme.ACCENT, 4)
            opt_rects.append((r, a))
            y += 62
        self._rects["_audio_opts"] = opt_rects

    def _scrim(self, screen) -> None:
        widgets.scrim(screen, size=SIZE)

    # ---- procedural glyphs (Nunito has no ♪ / ✓ symbols) -----------------

    def _glyph_check(self, screen, cx, cy, size, color, width) -> None:
        pygame.draw.lines(screen, color, False, [
            (cx - size * 0.45, cy + size * 0.02),
            (cx - size * 0.12, cy + size * 0.35),
            (cx + size * 0.48, cy - size * 0.40),
        ], width)

    def _glyph_note(self, screen, cx, cy, size, color) -> None:
        r = max(3, int(size * 0.22))
        head = (int(cx - size * 0.14), int(cy + size * 0.18))
        pygame.draw.circle(screen, color, head, r)
        stem_x = head[0] + r - 1
        sw = max(2, int(size * 0.10))
        pygame.draw.line(screen, color, (stem_x, head[1]), (stem_x, int(cy - size * 0.34)), sw)
        pygame.draw.line(screen, color, (stem_x, int(cy - size * 0.34)),
                         (int(stem_x + size * 0.24), int(cy - size * 0.16)), sw)
