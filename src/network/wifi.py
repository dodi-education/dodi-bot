"""Wi-Fi management via NetworkManager (``nmcli``).

Credentials are NOT stored by Dodi: on connect we hand the password to ``nmcli``,
which persists it root-only in ``/etc/NetworkManager/system-connections/`` and
auto-reconnects on boot. Nothing is written to ``.env`` or ``settings.json``.

Permissions on this Pi (verified): scanning/listing works unprivileged, but
``device wifi connect`` is polkit-``auth`` for a headless service, so connect goes
through ``sudo -n nmcli`` (passwordless sudo is configured). The blocking calls run
on worker threads (:class:`WifiController`) so the pygame render loop never stalls.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass

logger = logging.getLogger("DODI.network")


@dataclass
class Network:
    ssid: str
    signal: int            # 0-100
    secure: bool
    in_use: bool = False

    @property
    def bars(self) -> int:  # 0-4 strength bars
        s = self.signal
        return 4 if s >= 80 else 3 if s >= 60 else 2 if s >= 40 else 1 if s >= 20 else 0


@dataclass
class ConnectResult:
    ok: bool
    ssid: str
    error: str | None = None


def _split_terse(line: str) -> list[str]:
    """Split an ``nmcli -t`` line on unescaped ``:`` (nmcli escapes ``:`` and ``\\``)."""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def parse_scan(stdout: str) -> list[Network]:
    """Parse ``nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list`` output."""
    best: dict[str, Network] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = _split_terse(line)
        if len(parts) < 4:
            continue
        in_use, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue  # hidden network
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        net = Network(ssid=ssid, signal=sig,
                      secure=security not in ("", "--"),
                      in_use=in_use.strip() == "*")
        prev = best.get(ssid)
        if prev is None:
            best[ssid] = net
        else:
            # keep the strongest entry per SSID, but let "connected" stick
            keep = net if net.signal >= prev.signal else prev
            keep.in_use = prev.in_use or net.in_use
            best[ssid] = keep
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid.lower()))


def scan(rescan: bool = False, timeout: float = 15.0) -> list[Network]:
    """Return visible networks (strongest per SSID first). Best-effort."""
    if rescan:
        try:  # a fresh rescan is polkit-auth → needs sudo; ignore failures
            _run(["sudo", "-n", "nmcli", "device", "wifi", "rescan"], timeout=timeout)
        except Exception:
            pass
    try:
        cp = _run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                   "device", "wifi", "list", "--rescan", "no"], timeout=timeout)
    except Exception as e:
        logger.warning("wifi scan failed: %s", e)
        return []
    return parse_scan(cp.stdout)


def active_ssid() -> str | None:
    """SSID of the currently-connected network, or None."""
    for n in scan():
        if n.in_use:
            return n.ssid
    return None


def _classify_error(stderr: str) -> str:
    s = (stderr or "").lower()
    if "secrets were required" in s or "802-11-wireless-security" in s or "no key available" in s:
        return "Wrong password"
    if "timeout" in s or "timed out" in s:
        return "Connection timed out"
    if "no network with ssid" in s:
        return "Network not found"
    return "Could not connect"


def connect(ssid: str, password: str | None = None, timeout: float = 45.0) -> ConnectResult:
    """Join ``ssid`` via ``sudo -n nmcli`` (NetworkManager stores the secret root-only)."""
    args = ["sudo", "-n", "nmcli", "device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    try:
        cp = _run(args, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ConnectResult(False, ssid, "Connection timed out")
    except Exception as e:
        logger.warning("wifi connect failed: %s", e)
        return ConnectResult(False, ssid, "Could not connect")
    if cp.returncode == 0:
        return ConnectResult(True, ssid, None)
    return ConnectResult(False, ssid, _classify_error(cp.stderr))


class WifiController:
    """Runs scan/connect on worker threads; the UI polls :meth:`snapshot` each frame."""

    def __init__(self):
        self._lock = threading.Lock()
        self._networks: list[Network] = []
        self._scan_state = "idle"     # 'idle' | 'scanning' | 'done' | 'error'
        self._conn_state = "idle"     # 'idle' | 'connecting' | 'ok' | 'error'
        self._conn_ssid: str | None = None
        self._conn_error: str | None = None

    def start_scan(self, rescan: bool = True) -> None:
        with self._lock:
            if self._scan_state == "scanning":
                return
            self._scan_state = "scanning"
        threading.Thread(target=self._scan_worker, args=(rescan,), daemon=True).start()

    def _scan_worker(self, rescan: bool) -> None:
        nets = scan(rescan=rescan)
        with self._lock:
            self._networks = nets
            self._scan_state = "done"

    def start_connect(self, ssid: str, password: str | None) -> None:
        with self._lock:
            if self._conn_state == "connecting":
                return
            self._conn_state = "connecting"
            self._conn_ssid = ssid
            self._conn_error = None
        threading.Thread(target=self._connect_worker, args=(ssid, password), daemon=True).start()

    def _connect_worker(self, ssid: str, password: str | None) -> None:
        res = connect(ssid, password)
        with self._lock:
            self._conn_state = "ok" if res.ok else "error"
            self._conn_error = res.error
        if res.ok:
            self.start_scan(rescan=False)  # refresh the "connected" marker

    def reset_connect(self) -> None:
        with self._lock:
            self._conn_state = "idle"
            self._conn_ssid = None
            self._conn_error = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "scan_state": self._scan_state,
                "networks": list(self._networks),
                "conn_state": self._conn_state,
                "conn_ssid": self._conn_ssid,
                "conn_error": self._conn_error,
            }
