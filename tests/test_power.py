"""Power management routing and exact subprocess invocations."""

import subprocess

import poll_handling


def test_run_cmd_missing_binary(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("no such file")
    monkeypatch.setattr(poll_handling.subprocess, "run", boom)
    assert poll_handling._run_cmd("nonexistent", "x") is None


def test_run_cmd_success(monkeypatch):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(list(args), 0, stdout="out", stderr="")
    monkeypatch.setattr(poll_handling.subprocess, "run", run)
    proc = poll_handling._run_cmd("smartctl", "-l", "selftest", "-j", "/dev/sda")
    assert proc is not None
    assert proc.returncode == 0


def test_is_nvme():
    assert poll_handling._is_nvme("/dev/nvme0n1")
    assert poll_handling._is_nvme("/dev/nvme0n1p1")
    assert not poll_handling._is_nvme("/dev/sda")


def test_is_loop_or_mapped():
    for node in ("/dev/loop0", "/dev/dm-0", "/dev/ram0", "/dev/zram0", "/dev/md0"):
        assert poll_handling._is_loop_or_mapped(node), node
    assert not poll_handling._is_loop_or_mapped("/dev/sda")
    assert not poll_handling._is_loop_or_mapped("/dev/nvme0n1")


def test_apply_nvme_power_exact_commands(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

    monkeypatch.setattr(poll_handling, "_run_cmd", fake_run)
    poll_handling._apply_nvme_power("/dev/nvme0n1")
    assert calls == [
        ["nvme", "set-feature", "/dev/nvme0n1", "-n", "0xffffffff", "-f", "0x02", "-v", "0"],
        ["nvme", "set-feature", "/dev/nvme0n1", "-n", "0xffffffff", "-f", "0x0c", "-v", "0"],
    ]


def test_apply_nvme_power_handles_failure(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(list(args), 1, stdout="", stderr="NVMe status: Feature Not Namespace Specific")

    monkeypatch.setattr(poll_handling, "_run_cmd", fake_run)
    poll_handling._apply_nvme_power("/dev/nvme0n1")  # must not raise
    assert len(calls) == 2


def test_apply_hdparm_exact_commands(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

    monkeypatch.setattr(poll_handling, "_run_cmd", fake_run)
    poll_handling._apply_hdparm("/dev/sda")
    assert calls == [["hdparm", "-B", "255", "-S", "0", "/dev/sda"]]


def test_apply_power_routes_by_node_and_transport(monkeypatch):
    nvme_calls, hdparm_calls = [], []
    monkeypatch.setattr(poll_handling, "_apply_nvme_power", lambda n: nvme_calls.append(n))
    monkeypatch.setattr(poll_handling, "_apply_hdparm", lambda n: hdparm_calls.append(n))

    poll_handling._apply_power({"device": "/dev/nvme0n1"})
    poll_handling._apply_power({"device": "/dev/sdb", "transport": "nvme"})
    poll_handling._apply_power({"device": "/dev/sda"})
    poll_handling._apply_power({"device": "/dev/sda", "transport": "usb"})

    assert nvme_calls == ["/dev/nvme0n1", "/dev/sdb"]
    assert hdparm_calls == ["/dev/sda", "/dev/sda"]


def test_apply_power_skips_loop_and_mapped(monkeypatch):
    nvme_calls, hdparm_calls = [], []
    monkeypatch.setattr(poll_handling, "_apply_nvme_power", lambda n: nvme_calls.append(n))
    monkeypatch.setattr(poll_handling, "_apply_hdparm", lambda n: hdparm_calls.append(n))

    for node in ("/dev/loop0", "/dev/dm-0", "/dev/ram0", "/dev/md0"):
        poll_handling._apply_power({"device": node})

    assert nvme_calls == []
    assert hdparm_calls == []
