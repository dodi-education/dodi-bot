"""Wi-Fi management via NetworkManager (``nmcli``).

Credentials are NOT stored by Dodi: on connect we hand the password to ``nmcli``,
which persists it root-only in ``/etc/NetworkManager/system-connections/`` and
auto-reconnects on boot. Nothing is written to ``.env`` or ``settings.json``.

When a profile already exists for the target SSID we update and re-activate it in
place rather than calling ``device wifi connect`` — that command can delete a
working profile when a freshly-typed password is wrong. A new password is verified
on a throwaway clone first, so the saved network always survives a failed attempt.

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


_VERIFY_PROFILE = "dodi-verify"   # throwaway clone used to test a new password


def _saved_profile_for_ssid(ssid: str) -> str | None:
    """Name of an existing saved Wi-Fi profile whose SSID is ``ssid``, else None.

    Matches on the stored ``802-11-wireless.ssid`` (not the profile name), so the
    Raspberry Pi imager's ``preconfigured`` profile is found too. Read-only.
    """
    try:
        cp = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], timeout=10)
    except Exception as e:
        logger.warning("wifi profile lookup failed: %s", e)
        return None
    for line in cp.stdout.splitlines():
        parts = _split_terse(line)
        if len(parts) < 2 or parts[1] != "802-11-wireless":
            continue
        name = parts[0]
        try:
            got = _run(["nmcli", "-t", "-g", "802-11-wireless.ssid",
                        "connection", "show", name], timeout=10)
        except Exception:
            continue
        if got.stdout.strip() == ssid:
            return name
    return None


def _connect_new(ssid: str, password: str | None, timeout: float) -> ConnectResult:
    """Create + activate a fresh profile via ``device wifi connect``.

    Used only when nothing is saved for ``ssid``: on failure nmcli discards just the
    throwaway profile it created, so there is no working config to lose here.
    """
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


def _activate(name: str, ssid: str, timeout: float) -> ConnectResult:
    """``connection up`` an existing profile (never deletes it on failure)."""
    try:
        up = _run(["sudo", "-n", "nmcli", "connection", "up", name], timeout=timeout)
    except subprocess.TimeoutExpired:
        return ConnectResult(False, ssid, "Connection timed out")
    except Exception as e:
        logger.warning("wifi connect failed: %s", e)
        return ConnectResult(False, ssid, "Could not connect")
    if up.returncode == 0:
        return ConnectResult(True, ssid, None)
    return ConnectResult(False, ssid, _classify_error(up.stderr))


def _reconnect_saved(name: str, ssid: str, password: str | None,
                     timeout: float) -> ConnectResult:
    """Re-activate the saved profile ``name`` without ever risking it.

    No password → just re-activate with the stored secret. With a password we test
    it on a disposable clone of the profile; only once it authenticates do we write
    it back to the real profile. A wrong password leaves the saved profile — and its
    previously-working secret — completely untouched.
    """
    if not password:
        return _activate(name, ssid, timeout)

    _run(["sudo", "-n", "nmcli", "connection", "delete", _VERIFY_PROFILE], timeout=10)
    cl = _run(["sudo", "-n", "nmcli", "connection", "clone", name, _VERIFY_PROFILE],
              timeout=15)
    if cl.returncode != 0:
        logger.warning("wifi clone for password test failed: %s", cl.stderr)
        return ConnectResult(False, ssid, "Could not connect")
    _run(["sudo", "-n", "nmcli", "connection", "modify", _VERIFY_PROFILE,
          "wifi-sec.psk", password, "connection.autoconnect", "no"], timeout=15)
    try:
        up = _run(["sudo", "-n", "nmcli", "connection", "up", _VERIFY_PROFILE],
                  timeout=timeout)
        err = None if up.returncode == 0 else _classify_error(up.stderr)
    except subprocess.TimeoutExpired:
        err = "Connection timed out"
    except Exception as e:
        logger.warning("wifi connect failed: %s", e)
        err = "Could not connect"

    if err is not None:                       # bad password: drop the clone, keep the original
        _run(["sudo", "-n", "nmcli", "connection", "delete", _VERIFY_PROFILE], timeout=10)
        return ConnectResult(False, ssid, err)

    # Verified: write the good password to the real profile and switch to it.
    _run(["sudo", "-n", "nmcli", "connection", "modify", name,
          "wifi-sec.psk", password], timeout=15)
    res = _activate(name, ssid, timeout)
    _run(["sudo", "-n", "nmcli", "connection", "delete", _VERIFY_PROFILE], timeout=10)
    return res if res.ok else ConnectResult(True, ssid, None)


def connect(ssid: str, password: str | None = None, timeout: float = 45.0) -> ConnectResult:
    """Join ``ssid`` via ``sudo -n nmcli`` (NetworkManager stores the secret root-only).

    If a profile already exists for ``ssid`` it is updated/re-activated in place so a
    wrong password can never destroy the working config; otherwise a fresh one is made.
    """
    existing = _saved_profile_for_ssid(ssid)
    if existing is not None:
        return _reconnect_saved(existing, ssid, password, timeout)
    return _connect_new(ssid, password, timeout)


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
