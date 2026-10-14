"""Interface-agnostic connectivity check (Ethernet, Wi-Fi, USB tether, ...).

"Online" means the kernel has a usable default route — the same precondition any
internet request needs, regardless of which interface carries it. It is read
straight from ``/proc`` (no ``nmcli`` subprocess), so it's cheap enough to call
often. It doesn't prove the internet is reachable; callers still handle request
failures, but a missing default route reliably means "not connected".
"""

from __future__ import annotations

_RTF_UP = 0x0001


def _ipv4_default_route(path: str = "/proc/net/route") -> bool:
    # Columns: Iface Destination Gateway Flags RefCnt Use Metric Mask ...
    with open(path) as f:
        next(f, None)  # header
        for line in f:
            parts = line.split()
            if len(parts) < 8 or parts[0] == "lo":
                continue
            if parts[1] == "00000000" and parts[7] == "00000000" and int(parts[3], 16) & _RTF_UP:
                return True
    return False


def _ipv6_default_route(path: str = "/proc/net/ipv6_route") -> bool:
    # Columns: dest dest_plen src src_plen next_hop metric refcnt use flags iface
    # (no header). The kernel parks unreachable defaults on "lo"; skip those.
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 10 or parts[9] == "lo":
                continue
            if parts[0] == "0" * 32 and parts[1] == "00" and int(parts[8], 16) & _RTF_UP:
                return True
    return False


def has_default_route() -> bool:
    """True if any non-loopback interface has an active IPv4 or IPv6 default route."""
    for check in (_ipv4_default_route, _ipv6_default_route):
        try:
            if check():
                return True
        except (OSError, ValueError):
            continue
    return False
