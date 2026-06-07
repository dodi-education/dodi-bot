"""Wi-Fi nmcli parser tests (no subprocess — canned `nmcli -t` output)."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from network import wifi  # noqa: E402


def test_parse_scan_basic_fields():
    out = "\n".join([
        "*:Fritzbox-7590:87:WPA2",
        " :Dodi-Guest:54:",
        " :FlatWLAN:31:WPA1 WPA2",
    ])
    nets = wifi.parse_scan(out)
    by = {n.ssid: n for n in nets}
    assert by["Fritzbox-7590"].in_use is True
    assert by["Fritzbox-7590"].secure is True
    assert by["Dodi-Guest"].secure is False        # empty SECURITY => open
    assert by["FlatWLAN"].secure is True


def test_parse_scan_bars_buckets():
    out = "\n".join([" :A:85:WPA2", " :B:65:WPA2", " :C:45:WPA2", " :D:25:WPA2", " :E:5:WPA2"])
    bars = {n.ssid: n.bars for n in wifi.parse_scan(out)}
    assert (bars["A"], bars["B"], bars["C"], bars["D"], bars["E"]) == (4, 3, 2, 1, 0)


def test_parse_scan_dedupes_by_ssid_keeping_strongest_and_connected():
    out = "\n".join([" :Home:40:WPA2", "*:Home:30:WPA2", " :Home:70:WPA2"])
    nets = wifi.parse_scan(out)
    homes = [n for n in nets if n.ssid == "Home"]
    assert len(homes) == 1
    assert homes[0].signal == 70        # strongest kept
    assert homes[0].in_use is True      # connected flag sticks


def test_parse_scan_skips_hidden_and_sorts_connected_first():
    out = "\n".join([" ::60:WPA2", " :Zeta:90:WPA2", "*:Alpha:20:WPA2"])
    nets = wifi.parse_scan(out)
    assert [n.ssid for n in nets] == ["Alpha", "Zeta"]   # connected first, then by signal


def test_split_terse_unescapes_colons():
    assert wifi._split_terse(r"*:My\:Net:80:WPA2") == ["*", "My:Net", "80", "WPA2"]


def test_classify_error_messages():
    assert wifi._classify_error("Error: Secrets were required, but not provided") == "Wrong password"
    assert wifi._classify_error("Error: Connection activation timed out") == "Connection timed out"
    assert wifi._classify_error("No network with SSID 'x' found") == "Network not found"
    assert wifi._classify_error("weird") == "Could not connect"


def test_connect_builds_sudo_argv(monkeypatch):
    captured = {}

    class _CP:
        returncode = 0
        stderr = ""

    def fake_run(args, timeout):
        captured["args"] = args
        return _CP()

    monkeypatch.setattr(wifi, "_run", fake_run)
    res = wifi.connect("MyNet", "hunter2")
    assert res.ok is True
    assert captured["args"][:3] == ["sudo", "-n", "nmcli"]
    assert "password" in captured["args"] and "hunter2" in captured["args"]


def test_connect_open_network_omits_password(monkeypatch):
    captured = {}

    class _CP:
        returncode = 0
        stderr = ""

    def fake_run(args, timeout):
        captured["args"] = args
        return _CP()

    monkeypatch.setattr(wifi, "_run", fake_run)
    wifi.connect("OpenNet", None)
    assert "password" not in captured["args"]
