"""worker.gpu(): what nvidia-smi says goes into the heartbeat, an older driver and a missing card included."""
import subprocess

import pytest

import worker

NEWER = ("100, 37, 11468, 16376, 71, 284.53, 285.00, 2745, 10501, 62, P2, 4, 4, 16, 16, "
         "12, 3, 0x0000000000000004")
OLDER = "3, 1, 1210, 16376, 41, 22.10, 285.00, 210, 405, 0, P8, 1, 4, 16, 16"


@pytest.fixture(autouse=True)
def smi(monkeypatch):
    calls = []
    answers = {}

    def run(cmd, **kw):
        calls.append(cmd)
        query = cmd[1]
        for fields, (rc, out) in answers.items():
            if query == "--query-gpu=" + ",".join(fields):
                return subprocess.CompletedProcess(cmd, rc, out, "" if rc == 0 else "Field is not valid")
        return subprocess.CompletedProcess(cmd, 2, "", "Field is not valid")

    monkeypatch.setattr(worker.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(worker.subprocess, "run", run)
    monkeypatch.setattr(worker, "GPU_QUERY", None)
    return calls, answers


def test_newer_driver(smi):
    calls, answers = smi
    answers[tuple(worker.GPU_FIELDS + worker.GPU_NEWER)] = (0, NEWER + "\n")
    g = worker.gpu()
    assert g["utilization.gpu"] == 100 and g["memory.used"] == 11468 and g["power.draw"] == 284.53
    assert g["pstate"] == "P2" and g["clocks_event_reasons.active"] == "0x0000000000000004"
    assert g["utilization.encoder"] == 12 and g["utilization.decoder"] == 3 and g["pcie.link.width.current"] == 16
    assert len(calls) == 1


def test_older_driver_falls_back_and_remembers(smi):
    calls, answers = smi
    answers[tuple(worker.GPU_FIELDS)] = (0, OLDER)
    g = worker.gpu()
    assert g["pstate"] == "P8" and g["pcie.link.gen.current"] == 1 and "utilization.encoder" not in g
    assert len(calls) == 2
    worker.gpu()
    assert len(calls) == 3  # straight to the list that worked


def test_not_supported_values_are_left_out(smi):
    calls, answers = smi
    answers[tuple(worker.GPU_FIELDS)] = (0, OLDER.replace("0, P8", "[N/A], P8").replace(" 71", " [Not Supported]"))
    g = worker.gpu()
    assert "fan.speed" not in g and g["pstate"] == "P8"


def test_no_card_or_no_nvidia_smi(smi, monkeypatch):
    calls, answers = smi
    assert worker.gpu() is None  # every query fails: no card
    monkeypatch.setattr(worker.shutil, "which", lambda name: None)
    assert worker.gpu() is None


def test_hwinfo_is_windows_only():
    assert worker.os.name == "nt" or worker.hwinfo() is None


def test_gpu_sensors_keeps_the_cards_voltages_powers_and_temperatures():
    hw = [["Core 0 VID", "1.250 V"], ["GPU Core Voltage", "1.000 V"], ["GPU 12VHPWR Power", "41.249 W"],
          ["GPU Hot Spot Temperature", "57.1 °C"], ["GPU Clock", "2,640.0 MHz"], ["GPU Fan1", "0 RPM"],
          ["CPU Package", "45 °C"], ["GPU PCIe +12V Input Voltage", "12.512 V"]]
    assert [label for label, _ in worker.gpu_sensors(hw)] == [
        "GPU Core Voltage", "GPU 12VHPWR Power", "GPU Hot Spot Temperature", "GPU PCIe +12V Input Voltage"]
    assert worker.gpu_sensors(None) == []


def test_trace_is_windows_only(monkeypatch):
    monkeypatch.setattr(worker.os, "name", "posix")
    assert worker.trace("/nowhere", "test") is None  # returns at once instead of looping
