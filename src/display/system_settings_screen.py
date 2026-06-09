"""System settings screen — the two-finger swipe-up touch overlay.

Reproduces the Claude Design "Dodi-Bot Settings" page for the round 800x800 screen:
four sections (General / Internet / Power / Night) of sliders, switches and steppers,
plus Wi-Fi, Location and Night-schedule sub-sheets. Distinct from the alarm settings
(swipe-down). Day/night theme auto-selected by hour; accent #3E84BE; all icons are
real tabler.io SVGs (see :mod:`display.icons`).

Edits mutate the live ``settings`` dict and persist via the bound save callback;
settings that must take effect immediately are pushed through the avatar's
``_system_apply(key, value)`` hook (volume, display-power timers, night brightness/schedule).

Layout note: geometry is centered on ``CX`` and reuses the alarm screen's edge
insets (tab row near y=120, full-width cards 452px wide, close button 34px from the
bottom) — NOT the design HTML's raw pixels (which include a 22px device bezel that
the real screen lacks).
"""

from __future__ import annotations

import math
import time

import pygame

from . import icons, theme, widgets
from network import geocode as geocode_mod
from network import wifi as wifi_mod

# On-screen keyboard rows (QWERTZ, matching the design's compact keyboard).
KB_ROWS = ["1234567890", "qwertzuiop", "asdfghjkl", "yxcvbnm"]

SIZE = 800
CX = SIZE // 2

CARD_W = 452
CARD_X = CX - CARD_W // 2          # 174 — same horizontal inset as the alarm cards

DANGER = (210, 85, 63)            # #d2553f — destructive accent (Shut down) from the design

# (id, label, tabler-icon) — matches the design's SECTIONS.
SECTIONS = [
    ("general", "General", "volume"),
    ("internet", "Internet", "wifi"),
    ("power", "Power", "power"),
    ("night", "Night", "moon"),
]

# Stepper presets + formatter (from the design).
SECONDS_OPTS = [10, 15, 30, 45, 60, 90, 120, 300]
NIGHT_SECS_OPTS = [1, 3, 5, 10, 15, 30, 60]

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_2 = {"Mon": "Mo", "Tue": "Tu", "Wed": "We", "Thu": "Th", "Fri": "Fr", "Sat": "Sa", "Sun": "Su"}

# Slider key -> (settings path, vmin, vmax, live-apply key or None).
SLIDERS = {
    "master_vol": (("audio", "volume"), 0, 100, "audio.volume"),
    "alarm_vol": (("alarm", "volume"), 0, 100, None),
    "night_bri": (("night_mode", "display", "brightness"), 1, 100, "night_mode.display.brightness"),
}


def fmt_secs(s) -> str:
    s = int(s)
    if s >= 60:
        if s % 60 == 0:
            return f"{s // 60} min"
        return f"{s // 60}m {s % 60}s"
    return f"{s}s"


def _clamp01(f: float) -> float:
    return 0.0 if f < 0 else 1.0 if f > 1 else f


def _parse_span(ts: str):
    a, b = ts.split("-")
    fh, fm = a.split(":")
    th, tm = b.split(":")
    return int(fh), int(fm), int(th), int(tm)


def _fmt_span(fh, fm, th, tm) -> str:
    return f"{fh:02d}:{fm:02d}-{th:02d}:{tm:02d}"


class SystemSettingsScreen:
    """Stateful system-settings UI. Lives in (and is driven by) the avatar thread."""

    def __init__(self, avatar):
        self._avatar = avatar
        self.section = "general"      # 'general' | 'internet' | 'power' | 'night'
        self.sub = None               # None | 'wifi' | 'location' | 'schedule'
        self.theme_name = "day"
        self._bg = None
        self._rects: dict = {}
        self._has_touch = False
        self._drag = None             # active slider key, or None
        self._slider_geom: dict = {}  # key -> (x, w, vmin, vmax) for drag mapping
        # remembered seconds so a power switch can re-enable to its last value
        self._power_mem = {"off_day": 60, "off_night": 3}
        # schedule editor state
        self._sched_day = "Mon"
        self._sched_edit = None       # None | {'idx': int|'new', 'fh','fm','th','tm'}
        # wifi / location sub-sheets
        self._wifi = wifi_mod.WifiController()
        self._geo = geocode_mod.GeocodeController()
        self._wifi_target = None      # ssid being authenticated, or None (list view)
        self._wifi_pw = ""
        self._kb_shift = False
        self._loc_query = ""
        self._wifi_status = None       # last-known connected SSID (nav-row status)
        self._confirm = None           # None | 'restart' | 'shutdown' (power confirm dialog)

    # ---------------------------------------------------------------- state

    @property
    def _settings(self) -> dict:
        return self._avatar._settings or {}

    def _get(self, path, default=None):
        d = self._settings
        for k in path:
            if not isinstance(d, dict):
                return default
            d = d.get(k)
            if d is None:
                return default
        return d

    def _set_path(self, path, value) -> None:
        d = self._settings
        for k in path[:-1]:
            nxt = d.get(k)
            if not isinstance(nxt, dict):
                nxt = {}
                d[k] = nxt
            d = nxt
        d[path[-1]] = value

    def _save(self) -> None:
        av = self._avatar
        if getattr(av, "_save_settings", None) is not None and av._settings is not None:
            try:
                av._save_settings(av._settings)
            except Exception:
                pass

    def _apply_live(self, key, value) -> None:
        cb = getattr(self._avatar, "_system_apply", None)
        if cb is not None:
            try:
                cb(key, value)
            except Exception:
                pass

    def _set(self, path, value, apply_key=None, save=True) -> None:
        """Write a settings value; optionally live-apply and/or persist."""
        self._set_path(path, value)
        if apply_key:
            self._apply_live(apply_key, value)
        if save:
            self._save()

    def open(self, section: str = "general", sub: str | None = None) -> None:
        """(Re)initialise when the overlay is opened.

        ``section``/``sub`` let a caller land directly on a target — the info
        screen's weather hints open this at ``("internet", "location")`` (no
        location) or ``("internet", None)`` (offline). Defaults reproduce the
        plain swipe-up entry, so existing callers are unaffected.
        """
        valid = {sid for sid, _, _ in SECTIONS}
        self.section = section if section in valid else "general"
        self.sub = None
        self._confirm = None
        self._drag = None
        self._sched_edit = None
        self._wifi_target = None
        self._wifi_pw = ""
        self._loc_query = ""
        self._kb_shift = False
        self.theme_name = theme.auto_theme()
        self._bg = None  # rebuild gradient for the (possibly changed) theme
        # Cheap cached scan so the Internet row can show the connected SSID.
        self._wifi.start_scan(rescan=False)
        # Open straight to a sub-sheet, priming it the same way its nav row does.
        if sub == "location":
            self.sub = "location"
            self._geo.start_search("")
        elif sub == "wifi":
            self.sub = "wifi"
            self._wifi.reset_connect()
            self._wifi.start_scan()

    # --------------------------------------------------------- event input

    def _hit(self, name, xy) -> bool:
        r = self._rects.get(name)
        return bool(r and r.collidepoint(xy))

    def handle_event(self, event) -> None:
        t = event.type
        if t in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
            self._has_touch = True
        is_mouse = t in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION)
        if is_mouse and self._has_touch:
            return

        down = t in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN)
        move = t == pygame.FINGERMOTION or (t == pygame.MOUSEMOTION and any(pygame.mouse.get_pressed()))
        up = t in (pygame.FINGERUP, pygame.MOUSEBUTTONUP)

        xy = widgets.event_xy(event, SIZE)
        if xy is None:
            return

        # Modal power-confirm dialog owns all input while open.
        if self._confirm is not None:
            if down:
                if self._hit("confirm_cancel", xy):
                    self._confirm = None
                elif self._hit("confirm_go", xy):
                    action, self._confirm = self._confirm, None
                    import system_control
                    if action == "restart":
                        system_control.reboot()
                    elif action == "shutdown":
                        system_control.shutdown()
            return

        if self.sub is not None:
            if down:
                self._sub_event(xy)
            return

        # Active slider drag owns motion until release.
        if self._drag is not None:
            if move:
                self._slider_set(self._drag, xy)
            elif up:
                self._drag = None
                self._save()        # persist once at the end of the drag
            return

        if down:
            self._main_event(xy)
        elif up:
            self._drag = None

    def _main_event(self, xy) -> None:
        if self._hit("close", xy):
            self._avatar.close_settings()
            return
        for sid, _, _ in SECTIONS:
            if self._hit(f"section_{sid}", xy):
                self.section = sid
                self._drag = None
                return
        # Sliders (start a drag).
        for key in SLIDERS:
            if self._hit(f"slider_{key}", xy):
                self._drag = key
                self._slider_set(key, xy)
                return
        handler = getattr(self, f"_event_{self.section}", None)
        if handler is not None:
            handler(xy)

    def _slider_set(self, key, xy) -> None:
        geom = self._slider_geom.get(key)
        if geom is None:
            return
        x, w, vmin, vmax = geom
        frac = _clamp01((xy[0] - x) / max(1, w))
        v = round(vmin + frac * (vmax - vmin))
        path, _, _, apply_key = SLIDERS[key]
        # Live-apply for responsiveness, but defer the disk write to drag-end.
        self._set(path, v, apply_key=apply_key, save=False)

    # ------- per-section tap handlers (switches, steppers, nav rows) -------

    def _event_general(self, xy) -> None:
        if self._hit("switch_ascending", xy):
            cur = bool(self._get(("alarm", "ascending_volume", "active"), False))
            self._set(("alarm", "ascending_volume", "active"), not cur)

    def _event_power(self, xy) -> None:
        if self._hit("btn_restart", xy):
            self._confirm = "restart"
            return
        if self._hit("btn_shutdown", xy):
            self._confirm = "shutdown"
            return
        for mem_key, path, opts in (
            ("off_day", ("power_options", "turn_off_display"), SECONDS_OPTS),
            ("off_night", ("power_options", "turn_off_display_night_mode"), NIGHT_SECS_OPTS),
        ):
            apply_key = "power_options." + path[1]
            cur = self._get(path)
            if self._hit(f"switch_{mem_key}", xy):
                if cur is None:
                    self._set(path, self._power_mem[mem_key], apply_key=apply_key)
                else:
                    self._power_mem[mem_key] = cur
                    self._set(path, None, apply_key=apply_key)
                return
            if cur is not None:
                i = opts.index(cur) if cur in opts else 0
                if self._hit(f"step_{mem_key}_minus", xy) and i > 0:
                    v = opts[i - 1]
                    self._power_mem[mem_key] = v
                    self._set(path, v, apply_key=apply_key)
                    return
                if self._hit(f"step_{mem_key}_plus", xy) and i < len(opts) - 1:
                    v = opts[i + 1]
                    self._power_mem[mem_key] = v
                    self._set(path, v, apply_key=apply_key)
                    return

    def _event_night(self, xy) -> None:
        if self._hit("switch_night_enabled", xy):
            cur = bool(self._get(("night_mode", "enabled"), False))
            self._set(("night_mode", "enabled"), not cur, apply_key="night_mode.enabled")
            return
        if self._hit("switch_disable_wifi_night", xy):
            cur = bool(self._get(("night_mode", "disable_wifi"), False))
            self._set(("night_mode", "disable_wifi"), not cur, apply_key="night_mode.disable_wifi")
            return
        if self._hit("nav_schedule", xy):
            self.sub = "schedule"
            self._sched_edit = None
            return

    def _event_internet(self, xy) -> None:
        if self._hit("switch_disable_wifi", xy):
            cur = bool(self._get(("internet", "disable_wifi"), False))
            self._set(("internet", "disable_wifi"), not cur, apply_key="internet.disable_wifi")
            return
        if self._hit("nav_wifi", xy):
            self.sub = "wifi"
            self._wifi_target = None
            self._wifi_pw = ""
            self._wifi.reset_connect()
            self._wifi.start_scan()
            return
        if self._hit("nav_location", xy):
            self.sub = "location"
            self._loc_query = ""
            self._geo.start_search("")
            return

    # ---------------------------------------------------------------- draw

    def draw(self, screen) -> None:
        th = theme.THEMES[self.theme_name]
        if self._bg is None:
            self._bg = theme.make_gradient_bg(SIZE, SIZE, th["bg_top"], th["bg_bottom"])
        screen.blit(self._bg, (0, 0))
        self._rects = {}
        self._slider_geom = {}

        self._draw_title(screen, th)
        self._draw_section_tabs(screen, th)
        getattr(self, f"_draw_{self.section}")(screen, th)
        self._draw_close(screen, th)

        if self.sub == "schedule":
            self._draw_schedule(screen, th)
        elif self.sub == "wifi":
            self._draw_wifi(screen, th)
        elif self.sub == "location":
            self._draw_location(screen, th)

        if self._confirm is not None:
            self._draw_confirm(screen, th)

    def _draw_confirm(self, screen, th) -> None:
        """Centered Restart/Shut-down confirmation dialog (from the design)."""
        widgets.scrim(screen)
        is_shut = self._confirm == "shutdown"
        color = DANGER if is_shut else theme.ACCENT
        icon = "power" if is_shut else "refresh"
        title = "Shut down Dodi?" if is_shut else "Restart Dodi?"
        sub = ("Dodi will power off and stop all alarms." if is_shut
               else "Dodi will reboot in a few seconds.")

        cw, ch = 440, 300
        card = pygame.Rect(CX - cw // 2, CX - ch // 2, cw, ch)
        pygame.draw.rect(screen, th["surface"], card, border_radius=32)

        cyc = card.y + 66
        widgets.alpha_circle(screen, (CX, cyc), 42, (*color, 36))
        icons.blit(screen, icon, CX, cyc, 40, color)
        widgets.text(screen, title, 25, 900, th["ink"], center=(CX, card.y + 138))
        widgets.text(screen, sub, 15, 600, th["muted"], center=(CX, card.y + 172))

        gap = 14
        bw = (cw - 22 * 2 - gap) // 2
        bh = 56
        by = card.bottom - 22 - bh
        cancel = pygame.Rect(card.x + 22, by, bw, bh)
        go = pygame.Rect(cancel.right + gap, by, bw, bh)
        pygame.draw.rect(screen, th["ring"], cancel, width=2, border_radius=18)
        widgets.text(screen, "Cancel", 18, 800, th["muted"], center=cancel.center)
        pygame.draw.rect(screen, color, go, border_radius=18)
        widgets.text(screen, "Shut down" if is_shut else "Restart", 18, 800,
                     (255, 255, 255), center=go.center)
        self._rects["confirm_cancel"] = cancel
        self._rects["confirm_go"] = go

    def _draw_title(self, screen, th) -> None:
        widgets.text(screen, "SETTINGS", 16, 800, th["muted"], center=(CX, 88))

    def _draw_section_tabs(self, screen, th) -> None:
        tw, tabh, gap, top = 116, 60, 8, 116
        total = len(SECTIONS) * tw + (len(SECTIONS) - 1) * gap
        x = CX - total // 2
        for sid, label, icon in SECTIONS:
            r = pygame.Rect(x, top, tw, tabh)
            on = self.section == sid
            if on:
                pygame.draw.rect(screen, theme.ACCENT, r, border_radius=20)
            else:
                pygame.draw.rect(screen, th["surface"], r, border_radius=20)
                pygame.draw.rect(screen, th["ring"], r, width=2, border_radius=20)
            col = (255, 255, 255) if on else th["muted"]
            icons.blit(screen, icon, r.centerx, r.centery - 9, 23, col)
            widgets.text(screen, label, 13, 800, col, center=(r.centerx, r.centery + 18))
            self._rects[f"section_{sid}"] = r
            x += tw + gap

    # ---- card primitives ----

    def _card(self, screen, th, y, h) -> pygame.Rect:
        r = pygame.Rect(CARD_X, y, CARD_W, h)
        pygame.draw.rect(screen, th["surface"], r, border_radius=24)
        return r

    def _head(self, screen, th, card, icon, label, cy, sub=None) -> None:
        icons.blit(screen, icon, card.x + 22 + 12, cy, 24, theme.ACCENT)
        lx = card.x + 22 + 24 + 12
        if sub:
            widgets.text(screen, label, 20, 800, th["ink"], midleft=(lx, cy - 11))
            widgets.text(screen, sub, 14, 600, th["muted"], midleft=(lx, cy + 12))
        else:
            widgets.text(screen, label, 20, 800, th["ink"], midleft=(lx, cy))

    def _slider(self, screen, th, key, card, cy, value, vmin, vmax) -> None:
        x = card.x + 22 + 17
        w = card.w - 44 - 34
        frac = _clamp01((value - vmin) / max(1, (vmax - vmin)))
        pygame.draw.rect(screen, th["ring"], pygame.Rect(x, cy - 6, w, 12), border_radius=6)
        pygame.draw.rect(screen, theme.ACCENT, pygame.Rect(x, cy - 6, int(w * frac), 12), border_radius=6)
        kx = x + int(w * frac)
        pygame.draw.circle(screen, th["surface"], (kx, cy), 17)
        pygame.draw.circle(screen, theme.ACCENT, (kx, cy), 17, 5)
        self._rects[f"slider_{key}"] = pygame.Rect(x - 17, cy - 22, w + 34, 44)
        self._slider_geom[key] = (x, w, vmin, vmax)

    def _stepper(self, screen, th, key, right, cy, value, options, enabled=True) -> None:
        bw, vw = 42, 84
        total = 5 + bw + 6 + vw + 6 + bw + 5
        x0 = right - total
        cont = pygame.Rect(x0, cy - 26, total, 52)
        pygame.draw.rect(screen, th["surface2"], cont, border_radius=16)
        i = options.index(value) if value in options else 0
        mb = pygame.Rect(x0 + 5, cy - 21, bw, 42)
        pb = pygame.Rect(cont.right - 5 - bw, cy - 21, bw, 42)
        for rect, name, dead in ((mb, "minus", i == 0), (pb, "plus", i == len(options) - 1)):
            pygame.draw.rect(screen, th["surface"], rect, border_radius=12)
            col = th["ring"] if (dead or not enabled) else theme.ACCENT
            icons.blit(screen, name, rect.centerx, rect.centery, 22, col)
        vcol = th["ink"] if enabled else th["muted"]
        widgets.text(screen, fmt_secs(value), 18, 800, vcol, center=(x0 + 5 + bw + 6 + vw // 2, cy))
        self._rects[f"step_{key}_minus"] = mb
        self._rects[f"step_{key}_plus"] = pb

    def _nav_row(self, screen, th, y, h, icon, title, sub, status, key) -> pygame.Rect:
        r = pygame.Rect(CARD_X, y, CARD_W, h)
        pygame.draw.rect(screen, th["surface"], r, border_radius=24)
        icons.blit(screen, icon, r.x + 22 + 12, r.centery, 26, theme.ACCENT)
        lx = r.x + 22 + 26 + 12
        widgets.text(screen, title, 20, 800, th["ink"], midleft=(lx, r.centery - 11))
        if sub:
            widgets.text(screen, sub, 14, 600, th["muted"], midleft=(lx, r.centery + 12))
        chev_x = r.right - 22
        icons.blit(screen, "chevron-right", chev_x - 7, r.centery, 22, th["muted"])
        if status:
            stx = chev_x - 22
            surf = theme.font(15, 800).render(status, True, th["muted"])
            screen.blit(surf, surf.get_rect(midright=(stx, r.centery)))
        self._rects[key] = r
        return r

    def _stack_top(self, total_h) -> int:
        """Top y that vertically centers a `total_h` content stack in the panel."""
        return max(196, (190 + 690) // 2 - total_h // 2)

    # ---- sections ----

    def _draw_general(self, screen, th) -> None:
        gap = 14
        heights = [120, 120, 88]
        y = self._stack_top(sum(heights) + gap * 2)

        mv = int(self._get(("audio", "volume"), 0))
        c = self._card(screen, th, y, 120)
        self._head(screen, th, c, "volume", "Master volume", c.y + 34)
        widgets.text(screen, f"{mv}%", 19, 800, theme.ACCENT, center=(c.right - 40, c.y + 34))
        self._slider(screen, th, "master_vol", c, c.y + 82, mv, 0, 100)
        y += 120 + gap

        av_ = int(self._get(("alarm", "volume"), 0))
        c = self._card(screen, th, y, 120)
        self._head(screen, th, c, "bell", "Alarm volume", c.y + 34)
        widgets.text(screen, f"{av_}%", 19, 800, theme.ACCENT, center=(c.right - 40, c.y + 34))
        self._slider(screen, th, "alarm_vol", c, c.y + 82, av_, 0, 100)
        y += 120 + gap

        dur = int(self._get(("alarm", "ascending_volume", "duration"), 20))
        on = bool(self._get(("alarm", "ascending_volume", "active"), False))
        c = self._card(screen, th, y, 88)
        self._head(screen, th, c, "trending-up", "Ascending alarm", c.centery, sub=f"fades in over {dur}s")
        widgets.draw_switch(screen, th, c.right - 22 - 74, c.centery - 21, on)
        self._rects["switch_ascending"] = pygame.Rect(c.right - 22 - 74, c.centery - 21, 74, 42)

    def _power_button(self, screen, th, x, y, w, h, icon, label, color, key) -> None:
        """An outlined action button (icon over label) used by the Power section."""
        r = pygame.Rect(x, y, w, h)
        pygame.draw.rect(screen, th["surface"], r, border_radius=20)
        pygame.draw.rect(screen, color, r, width=2, border_radius=20)
        icons.blit(screen, icon, r.centerx, r.centery - 12, 26, color)
        widgets.text(screen, label, 16, 800, color, center=(r.centerx, r.centery + 18))
        self._rects[key] = r

    def _draw_power(self, screen, th) -> None:
        gap = 14
        btn_h = 72
        y = self._stack_top(148 * 2 + gap * 2 + btn_h)
        for mem_key, path, opts, icon, label in (
            ("off_day", ("power_options", "turn_off_display"), SECONDS_OPTS, "device-desktop", "Turn off display"),
            ("off_night", ("power_options", "turn_off_display_night_mode"), NIGHT_SECS_OPTS, "moon", "Display off at night"),
        ):
            cur = self._get(path)
            enabled = cur is not None
            c = self._card(screen, th, y, 148)
            self._head(screen, th, c, icon, label, c.y + 36)
            sw_x = c.right - 22 - 74
            widgets.draw_switch(screen, th, sw_x, c.y + 36 - 21, enabled)
            self._rects[f"switch_{mem_key}"] = pygame.Rect(sw_x, c.y + 36 - 21, 74, 42)
            widgets.text(screen, "After", 16, 700, th["muted"], midleft=(c.x + 22, c.y + 104))
            shown = cur if enabled else self._power_mem[mem_key]
            self._stepper(screen, th, mem_key, c.right - 22, c.y + 104, shown, opts, enabled=enabled)
            y += 148 + gap

        # Restart (accent) + Shut down (danger) action buttons.
        bw = (CARD_W - gap) // 2
        self._power_button(screen, th, CARD_X, y, bw, btn_h, "refresh", "Restart",
                           theme.ACCENT, "btn_restart")
        self._power_button(screen, th, CARD_X + bw + gap, y, bw, btn_h, "power", "Shut down",
                           DANGER, "btn_shutdown")

    def _draw_night(self, screen, th) -> None:
        gap = 14
        heights = [88, 120, 88, 78]
        y = self._stack_top(sum(heights) + gap * 3)

        on = bool(self._get(("night_mode", "enabled"), False))
        c = self._card(screen, th, y, 88)
        self._head(screen, th, c, "moon", "Night mode", c.centery, sub="Dim & calm at night")
        widgets.draw_switch(screen, th, c.right - 22 - 74, c.centery - 21, on)
        self._rects["switch_night_enabled"] = pygame.Rect(c.right - 22 - 74, c.centery - 21, 74, 42)
        y += 88 + gap

        bri = int(self._get(("night_mode", "display", "brightness"), 10))
        c = self._card(screen, th, y, 120)
        self._head(screen, th, c, "sun", "Night brightness", c.y + 34)
        widgets.text(screen, f"{bri}%", 19, 800, theme.ACCENT, center=(c.right - 40, c.y + 34))
        self._slider(screen, th, "night_bri", c, c.y + 82, bri, 1, 100)
        y += 120 + gap

        dw = bool(self._get(("night_mode", "disable_wifi"), False))
        c = self._card(screen, th, y, 88)
        self._head(screen, th, c, "wifi-off", "Disable Wi-Fi at night", c.centery,
                   sub="Quieter, lower power")
        widgets.draw_switch(screen, th, c.right - 22 - 74, c.centery - 21, dw)
        self._rects["switch_disable_wifi_night"] = pygame.Rect(c.right - 22 - 74, c.centery - 21, 74, 42)
        y += 88 + gap

        sched = self._get(("night_mode", "schedule"), {}) or {}
        ndays = sum(1 for d in DAYS if sched.get(d))
        self._nav_row(screen, th, y, 78, "calendar", "Schedule",
                      "When night mode is active", f"{ndays} days", "nav_schedule")

    def _draw_internet(self, screen, th) -> None:
        gap = 14
        enabled = not bool(self._get(("internet", "disable_wifi"), False))
        total = 88 + gap + (78 + gap if enabled else 0) + 78
        y = self._stack_top(total)

        # Wi-Fi master toggle (off => radio powered down, network row hidden).
        c = self._card(screen, th, y, 88)
        self._head(screen, th, c, "wifi" if enabled else "wifi-off", "Wi-Fi",
                   c.centery, sub="On" if enabled else "Disabled")
        sw_x = c.right - 22 - 74
        widgets.draw_switch(screen, th, sw_x, c.centery - 21, enabled)
        self._rects["switch_disable_wifi"] = pygame.Rect(sw_x, c.centery - 21, 74, 42)
        y += 88 + gap

        # Network selection — hidden while Wi-Fi is disabled.
        if enabled:
            snap = self._wifi.snapshot()
            connected = next((n.ssid for n in snap["networks"] if n.in_use), None) or self._wifi_status
            if connected:
                status, sub = connected, "Connected"
            elif snap["scan_state"] in ("idle", "scanning"):
                status, sub = "…", "Connected network"
            else:
                status, sub = "Not connected", "Connected network"
            self._nav_row(screen, th, y, 78, "search", "Network", sub, status, "nav_wifi")
            y += 78 + gap

        loc = self._get(("weather", "location_name"), "None")
        self._nav_row(screen, th, y, 78, "map-pin", "Location", "For weather forecast",
                      loc, "nav_location")

    def _draw_close(self, screen, th) -> None:
        self._rects["close"] = widgets.close_button(screen, th)

    # ----------------------------------------------------- schedule sheet

    def _sub_event(self, xy) -> None:
        if self.sub == "schedule":
            self._schedule_event(xy)
        elif self.sub == "wifi":
            self._wifi_event(xy)
        elif self.sub == "location":
            self._location_event(xy)

    def _sched_spans(self) -> list:
        sched = self._get(("night_mode", "schedule"), {}) or {}
        return list(sched.get(self._sched_day, []) or [])

    def _sched_write(self, spans) -> None:
        sched = dict(self._get(("night_mode", "schedule"), {}) or {})
        sched[self._sched_day] = spans
        self._set(("night_mode", "schedule"), sched, apply_key="night_mode.schedule")

    def _schedule_event(self, xy) -> None:
        if self._hit("sched_close", xy):
            self.sub = None
            self._sched_edit = None
            return
        consumed = (self._sched_edit_event(xy) if self._sched_edit is not None
                    else self._sched_list_event(xy))
        if consumed:
            return
        # A tap outside the panel dismisses the sheet (the scrim).
        panel = self._rects.get("sched_panel")
        if panel is not None and not panel.collidepoint(xy):
            self.sub = None
            self._sched_edit = None

    def _sched_list_event(self, xy) -> bool:
        for d in DAYS:
            if self._hit(f"sched_day_{d}", xy):
                self._sched_day = d
                return True
        spans = self._sched_spans()
        for i in range(len(spans)):
            if self._hit(f"sched_del_{i}", xy):
                del spans[i]
                self._sched_write(spans)
                return True
            if self._hit(f"sched_chip_{i}", xy):
                fh, fm, th_, tm = _parse_span(spans[i]["timespan"])
                self._sched_edit = {"idx": i, "fh": fh, "fm": fm, "th": th_, "tm": tm}
                return True
        if self._hit("sched_add", xy):
            self._sched_edit = {"idx": "new", "fh": 22, "fm": 0, "th": 23, "tm": 0}
            return True
        return False

    def _sched_edit_event(self, xy) -> bool:
        e = self._sched_edit
        if self._hit("sched_back", xy):
            self._sched_edit = None
            return True
        for field, dh, dm in (("from_h", 1, 0), ("from_m", 0, 15), ("to_h", 1, 0), ("to_m", 0, 15)):
            if self._hit(f"sched_{field}_up", xy):
                self._bump_edit(field, dh, dm)
                return True
            if self._hit(f"sched_{field}_dn", xy):
                self._bump_edit(field, -dh, -dm)
                return True
        if self._hit("sched_save", xy):
            span = {"timespan": _fmt_span(e["fh"], e["fm"], e["th"], e["tm"])}
            spans = self._sched_spans()
            if e["idx"] == "new":
                spans.append(span)
            else:
                spans[e["idx"]] = span
            spans.sort(key=lambda s: _parse_span(s["timespan"])[0] * 60 + _parse_span(s["timespan"])[1])
            self._sched_write(spans)
            self._sched_edit = None
            return True
        if self._hit("sched_remove", xy):
            spans = self._sched_spans()
            if e["idx"] != "new" and 0 <= e["idx"] < len(spans):
                del spans[e["idx"]]
                self._sched_write(spans)
            self._sched_edit = None
            return True
        return False

    def _bump_edit(self, field, dh, dm) -> None:
        e = self._sched_edit
        if field == "from_h":
            e["fh"] = (e["fh"] + dh + 24) % 24
        elif field == "from_m":
            e["fm"] = (e["fm"] + dm + 60) % 60
        elif field == "to_h":
            e["th"] = (e["th"] + dh + 24) % 24
        elif field == "to_m":
            e["tm"] = (e["tm"] + dm + 60) % 60

    def _draw_schedule(self, screen, th) -> None:
        widgets.scrim(screen, size=SIZE)
        self._rects["sub_scrim"] = pygame.Rect(0, 0, SIZE, SIZE)
        editing = self._sched_edit is not None
        pw, ph = 472, 470
        panel = pygame.Rect(CX - pw // 2, (SIZE - ph) // 2, pw, ph)
        pygame.draw.rect(screen, th["surface"], panel, border_radius=30)
        self._rects["sched_panel"] = panel

        # header
        title = "Edit period" if editing else "Night schedule"
        widgets.text(screen, title, 21, 900, th["ink"], center=(CX, panel.y + 34))
        cl = pygame.Rect(panel.right - 22 - 42, panel.y + 14, 42, 42)
        pygame.draw.circle(screen, th["surface2"], cl.center, 21)
        icons.blit(screen, "x", cl.centerx, cl.centery, 22, th["ink"])
        self._rects["sched_close"] = cl
        if editing:
            bk = pygame.Rect(panel.x + 22, panel.y + 14, 42, 42)
            pygame.draw.circle(screen, th["surface2"], bk.center, 21)
            icons.blit(screen, "arrow-left", bk.centerx, bk.centery, 22, th["ink"])
            self._rects["sched_back"] = bk
            self._draw_sched_editor(screen, th, panel)
        else:
            self._draw_sched_list(screen, th, panel)

    def _draw_sched_list(self, screen, th, panel) -> None:
        # day tabs
        pad, gap = 22, 6
        n = len(DAYS)
        avail = panel.w - 2 * pad - (n - 1) * gap
        tw = avail // n
        x = panel.x + pad
        ty = panel.y + 74
        for d in DAYS:
            r = pygame.Rect(x, ty, tw, 46)
            on = d == self._sched_day
            if on:
                pygame.draw.rect(screen, theme.ACCENT, r, border_radius=14)
            else:
                pygame.draw.rect(screen, th["surface"], r, border_radius=14)
                pygame.draw.rect(screen, th["ring"], r, width=2, border_radius=14)
            widgets.text(screen, DAY_2[d], 15, 800, (255, 255, 255) if on else th["muted"],
                         center=r.center)
            self._rects[f"sched_day_{d}"] = r
            x += tw + gap

        spans = self._sched_spans()
        y = ty + 46 + 18
        for i, s in enumerate(spans):
            fh, fm, th_, tm = _parse_span(s["timespan"])
            chip = pygame.Rect(panel.x + pad, y, panel.w - 2 * pad - 10 - 58, 56)
            pygame.draw.rect(screen, th["surface2"], chip, border_radius=16)
            icons.blit(screen, "moon", chip.x + 22, chip.centery, 18, theme.ACCENT)
            widgets.text(screen, f"{fh:02d}:{fm:02d} – {th_:02d}:{tm:02d}", 19, 800, th["ink"],
                         midleft=(chip.x + 44, chip.centery))
            self._rects[f"sched_chip_{i}"] = chip
            db = pygame.Rect(chip.right + 10, y, 58, 56)
            pygame.draw.rect(screen, th["surface2"], db, border_radius=16)
            icons.blit(screen, "trash", db.centerx, db.centery, 20, th["muted"])
            self._rects[f"sched_del_{i}"] = db
            y += 56 + 10
        if not spans:
            widgets.text(screen, f"No night periods on {self._sched_day}", 16, 700,
                         th["muted"], center=(panel.centerx, y + 8))
            y += 40

        add = pygame.Rect(panel.x + pad, min(y, panel.bottom - 22 - 52), panel.w - 2 * pad, 52)
        pygame.draw.rect(screen, theme.ACCENT, add, width=2, border_radius=16)
        icons.blit(screen, "plus", add.centerx - 52, add.centery, 20, theme.ACCENT)
        widgets.text(screen, "Add period", 17, 800, theme.ACCENT, midleft=(add.centerx - 28, add.centery))
        self._rects["sched_add"] = add

    def _draw_sched_editor(self, screen, th, panel) -> None:
        e = self._sched_edit
        cy = panel.y + 150
        for label, hx, h, m, fh_key, fm_key in (
            ("FROM", panel.centerx - 130, e["fh"], e["fm"], "from_h", "from_m"),
            ("TO", panel.centerx + 130, e["th"], e["tm"], "to_h", "to_m"),
        ):
            box = pygame.Rect(hx - 110, cy - 70, 220, 150)
            pygame.draw.rect(screen, th["surface2"], box, border_radius=20)
            widgets.text(screen, label, 13, 800, th["muted"], center=(box.centerx, box.y + 22))
            self._draw_time_col(screen, th, box.centerx - 52, box.centery + 12, h, fh_key)
            widgets.text(screen, ":", 28, 900, th["muted"], center=(box.centerx, box.centery + 12))
            self._draw_time_col(screen, th, box.centerx + 52, box.centery + 12, m, fm_key)
        # chevron between boxes
        icons.blit(screen, "chevron-right", panel.centerx, cy + 12, 26, th["muted"])

        # buttons
        by = panel.bottom - 22 - 52
        save = pygame.Rect(panel.centerx + 8, by, 150, 52)
        pygame.draw.rect(screen, theme.ACCENT, save, border_radius=16)
        widgets.text(screen, "Save", 18, 800, (255, 255, 255), center=save.center)
        self._rects["sched_save"] = save
        rem = pygame.Rect(panel.centerx - 8 - 150, by, 150, 52)
        pygame.draw.rect(screen, th["ring"], rem, width=2, border_radius=16)
        icons.blit(screen, "trash", rem.centerx - 36, rem.centery, 20, th["muted"])
        widgets.text(screen, "Remove", 17, 800, th["muted"], midleft=(rem.centerx - 14, rem.centery))
        self._rects["sched_remove"] = rem

    def _draw_time_col(self, screen, th, cx, cy, value, field) -> None:
        up = pygame.Rect(cx - 22, cy - 56, 44, 36)
        dn = pygame.Rect(cx - 22, cy + 20, 44, 36)
        for rect, name in ((up, "plus"), (dn, "minus")):
            pygame.draw.rect(screen, th["surface"], rect, border_radius=11)
            icons.blit(screen, name, rect.centerx, rect.centery, 18, theme.ACCENT)
        widgets.text(screen, f"{value:02d}", 30, 900, th["ink"], center=(cx, cy))
        self._rects[f"sched_{field}_up"] = up
        self._rects[f"sched_{field}_dn"] = dn

    # ---------------------------------------------- shared sheet chrome

    def _sheet(self, screen, th, w, h) -> pygame.Rect:
        widgets.scrim(screen, size=SIZE)
        self._rects["sub_scrim"] = pygame.Rect(0, 0, SIZE, SIZE)
        panel = pygame.Rect(CX - w // 2, (SIZE - h) // 2, w, h)
        pygame.draw.rect(screen, th["surface"], panel, border_radius=30)
        self._rects["sheet_panel"] = panel
        return panel

    def _sheet_header(self, screen, th, panel, title, back=False) -> None:
        widgets.text(screen, title, 21, 900, th["ink"], center=(panel.centerx, panel.y + 34))
        cl = pygame.Rect(panel.right - 22 - 42, panel.y + 14, 42, 42)
        pygame.draw.circle(screen, th["surface2"], cl.center, 21)
        icons.blit(screen, "x", cl.centerx, cl.centery, 22, th["ink"])
        self._rects["sheet_close"] = cl
        if back:
            bk = pygame.Rect(panel.x + 22, panel.y + 14, 42, 42)
            pygame.draw.circle(screen, th["surface2"], bk.center, 21)
            icons.blit(screen, "arrow-left", bk.centerx, bk.centery, 22, th["ink"])
            self._rects["sheet_back"] = bk

    def _spinner(self, screen, th, cx, cy, r, color) -> None:
        t = time.monotonic() * 3.0
        pygame.draw.circle(screen, th["ring"], (cx, cy), r, 5)
        rect = pygame.Rect(cx - r, cy - r, 2 * r, 2 * r)
        pygame.draw.arc(screen, color, rect, t, t + 1.8, 5)

    def _signal_bars(self, screen, th, cx, cy, level, color) -> None:
        bw, gap = 5, 4
        base_x = cx - (4 * bw + 3 * gap) // 2
        for i in range(4):
            bh = 6 + i * 5
            r = pygame.Rect(base_x + i * (bw + gap), cy + 11 - bh, bw, bh)
            pygame.draw.rect(screen, color if i < level else th["ring"], r, border_radius=2)

    _KB_H = 5 * 44 + 4 * 6  # keyboard block height

    def _draw_keyboard(self, screen, th, panel, action_label) -> None:
        kw, kh, gap, keyw = 514, 44, 6, 46
        kx = panel.centerx - kw // 2
        y = panel.bottom - 16 - self._KB_H
        keys = []

        def cap(rect, label, fill, fg, fsize=19):
            pygame.draw.rect(screen, fill, rect, border_radius=11)
            if label:
                widgets.text(screen, label, fsize, 700, fg, center=rect.center)

        for ri in range(3):
            row = KB_ROWS[ri]
            n = len(row)
            x = panel.centerx - (n * keyw + (n - 1) * gap) // 2
            for ch in row:
                rect = pygame.Rect(x, y, keyw, kh)
                disp = ch.upper() if self._kb_shift else ch
                cap(rect, disp, th["surface2"], th["ink"])
                keys.append((rect, disp))
                x += keyw + gap
            y += kh + gap

        row = KB_ROWS[3]
        n = len(row)
        sp = 64
        rw = sp + gap + n * keyw + (n - 1) * gap + gap + sp
        x = panel.centerx - rw // 2
        sh = pygame.Rect(x, y, sp, kh)
        cap(sh, "", theme.ACCENT if self._kb_shift else th["surface2"], th["ink"])
        icons.blit(screen, "chevron-up", sh.centerx, sh.centery, 22,
                   (255, 255, 255) if self._kb_shift else th["muted"])
        self._rects["kb_shift"] = sh
        x += sp + gap
        for ch in row:
            rect = pygame.Rect(x, y, keyw, kh)
            disp = ch.upper() if self._kb_shift else ch
            cap(rect, disp, th["surface2"], th["ink"])
            keys.append((rect, disp))
            x += keyw + gap
        bk = pygame.Rect(x, y, sp, kh)
        cap(bk, "", th["surface2"], th["ink"])
        icons.blit(screen, "backspace", bk.centerx, bk.centery, 22, th["muted"])
        self._rects["kb_back"] = bk
        y += kh + gap

        space_w = int(kw * 0.6)
        sprect = pygame.Rect(kx, y, space_w, kh)
        cap(sprect, "space", th["surface2"], th["muted"], fsize=15)
        self._rects["kb_space"] = sprect
        act = pygame.Rect(kx + space_w + gap, y, kw - space_w - gap, kh)
        pygame.draw.rect(screen, theme.ACCENT, act, border_radius=11)
        widgets.text(screen, action_label, 17, 800, (255, 255, 255), center=act.center)
        self._rects["kb_action"] = act
        self._rects["_kb_keys"] = keys

    def _kb_event(self, xy, on_char, on_back, on_action) -> bool:
        for rect, ch in self._rects.get("_kb_keys", []):
            if rect.collidepoint(xy):
                on_char(ch)
                self._kb_shift = False
                return True
        if self._hit("kb_shift", xy):
            self._kb_shift = not self._kb_shift
            return True
        if self._hit("kb_back", xy):
            on_back()
            return True
        if self._hit("kb_space", xy):
            on_char(" ")
            return True
        if self._hit("kb_action", xy):
            on_action()
            return True
        return False

    # ----------------------------------------------------- Wi-Fi sheet

    def _draw_wifi(self, screen, th) -> None:
        snap = self._wifi.snapshot()
        if snap["conn_state"] == "ok":
            self._wifi_status = snap["conn_ssid"]
            self._wifi.reset_connect()
            self.sub = None
            return
        panel = self._sheet(screen, th, 540, 588)
        target = self._wifi_target
        self._sheet_header(screen, th, panel, target or "Wi-Fi", back=bool(target))
        if target is None:
            self._draw_wifi_list(screen, th, panel, snap)
        else:
            self._draw_wifi_auth(screen, th, panel, snap)

    def _draw_wifi_list(self, screen, th, panel, snap) -> None:
        nets = snap["networks"]
        if snap["scan_state"] == "scanning" and not nets:
            self._spinner(screen, th, panel.centerx, panel.centery - 10, 22, theme.ACCENT)
            widgets.text(screen, "Scanning…", 16, 700, th["muted"],
                         center=(panel.centerx, panel.centery + 40))
            return
        if not nets:
            widgets.text(screen, "No networks found", 17, 700, th["muted"],
                         center=(panel.centerx, panel.centery))
            return
        pad, rowh = 22, 60
        y = panel.y + 74
        maxrows = max(1, int((panel.bottom - 18 - y) // rowh))
        rows = []
        for n in nets[:maxrows]:
            r = pygame.Rect(panel.x + pad, y, panel.w - 2 * pad, rowh - 6)
            if n.in_use:
                pygame.draw.rect(screen, th["surface2"], r, border_radius=16)
            self._signal_bars(screen, th, r.x + 24, r.centery, n.bars,
                              theme.ACCENT if n.in_use else th["ink"])
            widgets.text(screen, n.ssid, 19, 700, th["ink"], midleft=(r.x + 52, r.centery))
            rx = r.right - 14
            if n.secure:
                icons.blit(screen, "lock", rx - 9, r.centery, 17, th["muted"])
                rx -= 28
            if n.in_use:
                surf = theme.font(14, 800).render("Connected", True, theme.ACCENT)
                screen.blit(surf, surf.get_rect(midright=(rx, r.centery)))
            rows.append((r, n.ssid, n.secure, n.in_use))
            y += rowh
        self._rects["_wifi_rows"] = rows

    def _draw_wifi_auth(self, screen, th, panel, snap) -> None:
        fld = pygame.Rect(panel.x + 22, panel.y + 74, panel.w - 44, 54)
        pygame.draw.rect(screen, th["surface2"], fld, border_radius=16)
        icons.blit(screen, "lock", fld.x + 22, fld.centery, 20, th["muted"])
        if snap["conn_state"] == "connecting":
            widgets.text(screen, "Connecting…", 19, 800, th["ink"], midleft=(fld.x + 44, fld.centery))
            self._spinner(screen, th, panel.centerx, panel.centery + 40, 24, theme.ACCENT)
            return
        if self._wifi_pw:
            widgets.text(screen, "•" * len(self._wifi_pw), 22, 800, th["ink"],
                         midleft=(fld.x + 44, fld.centery))
        else:
            widgets.text(screen, "Password", 18, 600, th["muted"], midleft=(fld.x + 44, fld.centery))
        if snap["conn_state"] == "error" and snap["conn_error"]:
            widgets.text(screen, snap["conn_error"], 15, 800, (206, 92, 80),
                         center=(panel.centerx, fld.bottom + 16))
        self._draw_keyboard(screen, th, panel, "Connect")

    def _wifi_event(self, xy) -> None:
        if self._hit("sheet_close", xy):
            self.sub = None
            return
        snap = self._wifi.snapshot()
        if self._wifi_target is None:
            for rect, ssid, secure, in_use in self._rects.get("_wifi_rows", []):
                if rect.collidepoint(xy):
                    if in_use:
                        self.sub = None
                    elif secure:
                        self._wifi_target = ssid
                        self._wifi_pw = ""
                        self._wifi.reset_connect()
                    else:
                        self._wifi_target = ssid
                        self._wifi.start_connect(ssid, None)
                    return
            if not self._hit("sheet_panel", xy):
                self.sub = None
            return
        if self._hit("sheet_back", xy):
            self._wifi_target = None
            self._wifi_pw = ""
            self._wifi.reset_connect()
            return
        if snap["conn_state"] == "connecting":
            return

        def on_char(ch):
            self._wifi_pw += ch

        def on_back():
            self._wifi_pw = self._wifi_pw[:-1]

        def on_action():
            if self._wifi_pw:
                self._wifi.start_connect(self._wifi_target, self._wifi_pw)

        if self._kb_event(xy, on_char, on_back, on_action):
            return
        if not self._hit("sheet_panel", xy):
            self.sub = None

    # --------------------------------------------------- Location sheet

    def _draw_location(self, screen, th) -> None:
        panel = self._sheet(screen, th, 540, 588)
        self._sheet_header(screen, th, panel, "Location")
        fld = pygame.Rect(panel.x + 22, panel.y + 70, panel.w - 44, 54)
        pygame.draw.rect(screen, th["surface2"], fld, border_radius=16)
        icons.blit(screen, "search", fld.x + 22, fld.centery, 20, th["muted"])
        if self._loc_query:
            widgets.text(screen, self._loc_query, 18, 700, th["ink"], midleft=(fld.x + 44, fld.centery))
        else:
            widgets.text(screen, "Search city", 18, 600, th["muted"], midleft=(fld.x + 44, fld.centery))

        snap = self._geo.snapshot()
        y = fld.bottom + 12
        kbtop = panel.bottom - 16 - self._KB_H
        rowh = 56
        maxrows = max(0, int((kbtop - y) // rowh))
        if snap["state"] == "searching" and not snap["results"]:
            self._spinner(screen, th, panel.centerx, y + 26, 18, theme.ACCENT)
        rows = []
        cur = self._get(("weather", "location_name"))
        for p in snap["results"][:maxrows]:
            r = pygame.Rect(panel.x + 22, y, panel.w - 44, rowh - 6)
            on = p.name == cur
            if on:
                pygame.draw.rect(screen, th["surface2"], r, border_radius=16)
            icons.blit(screen, "map-pin", r.x + 22, r.centery, 22, theme.ACCENT if on else th["muted"])
            widgets.text(screen, p.name, 19, 800, th["ink"], midleft=(r.x + 48, r.centery - 9))
            widgets.text(screen, p.subtitle, 13, 700, th["muted"], midleft=(r.x + 48, r.centery + 11))
            if on:
                icons.blit(screen, "check", r.right - 24, r.centery, 20, theme.ACCENT)
            rows.append((r, p))
            y += rowh
        self._rects["_loc_rows"] = rows
        self._draw_keyboard(screen, th, panel, "Done")

    def _location_event(self, xy) -> None:
        if self._hit("sheet_close", xy):
            self.sub = None
            return
        for rect, p in self._rects.get("_loc_rows", []):
            if rect.collidepoint(xy):
                self._set_path(("weather", "location_name"), p.name)
                self._set_path(("weather", "country_code"), p.country_code)
                self._set_path(("weather", "lat"), p.lat)
                self._set_path(("weather", "lon"), p.lon)
                self._save()
                self.sub = None
                return

        def on_char(ch):
            self._loc_query += ch
            self._geo.start_search(self._loc_query)

        def on_back():
            self._loc_query = self._loc_query[:-1]
            self._geo.start_search(self._loc_query)

        def on_action():
            self.sub = None

        if self._kb_event(xy, on_char, on_back, on_action):
            return
        if not self._hit("sheet_panel", xy):
            self.sub = None
