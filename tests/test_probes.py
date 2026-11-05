"""Probe parsing, with the two primitives (``_read_text`` / ``_run``) mocked.

Each test fixes the machine it is describing, so the assertions are the same on
Linux, macOS and Windows CI. The last group checks the property that matters most
for a plugin that injects text into every prompt: a probe that fails is recorded
as ``unknown`` and never escapes as an exception.
"""

from __future__ import annotations

import pytest

from specs import probes


# ────────────────────────────────────────────────────────────── memory / cpu


MEMINFO = """MemTotal:       16384000 kB
MemFree:          512000 kB
MemAvailable:    8388608 kB
Buffers:          100000 kB
Cached:          2000000 kB
"""


def test_memory_prefers_memavailable(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")
    monkeypatch.setattr(probes, "_read_text", lambda path, limit=65536: MEMINFO)
    result = probes.probe_memory(settings)
    assert result["status"] == "measured"
    assert result["data"]["total_bytes"] == 16384000 * 1024
    assert result["data"]["available_bytes"] == 8388608 * 1024
    assert result["data"]["estimate"] is False
    assert any("MemAvailable" in item for item in result["evidence"])


def test_memory_falls_back_to_free_plus_cache_and_marks_it(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")
    text = "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 200 kB\n"
    monkeypatch.setattr(probes, "_read_text", lambda path, limit=65536: text)
    result = probes.probe_memory(settings)
    assert result["data"]["available_bytes"] == 350 * 1024
    assert result["data"]["estimate"] is True


def test_memory_without_meminfo_is_unknown_not_zero(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")
    monkeypatch.setattr(probes, "_read_text", lambda path, limit=65536: "")
    result = probes.probe_memory(settings)
    assert result["status"] == "unknown"
    assert result["data"]["total_bytes"] is None


CPUINFO_X86 = """processor\t: 0
model name\t: AMD Ryzen 9 7950X 16-Core Processor
processor\t: 1
model name\t: AMD Ryzen 9 7950X 16-Core Processor
"""

CPUINFO_ARM = """processor\t: 0
CPU implementer\t: 0x41
CPU part\t: 0xd0c
processor\t: 1
CPU implementer\t: 0x41
CPU part\t: 0xd0c
"""


def test_cpu_reads_model_name_and_counts_sockets(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")
    monkeypatch.setattr(probes._platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(probes.os, "cpu_count", lambda: None)
    monkeypatch.setattr(probes, "_read_text",
                        lambda path, limit=65536: CPUINFO_X86 if "cpuinfo" in str(path) else "")
    monkeypatch.setattr(probes.shutil, "which", lambda name: None)
    result = probes.probe_cpu(settings)
    assert result["data"]["model"] == "AMD Ryzen 9 7950X 16-Core Processor"
    assert result["data"]["cores"] == 2
    assert result["status"] == "measured"


def test_cpu_maps_an_arm_part_number_to_a_name(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")
    monkeypatch.setattr(probes._platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(probes.os, "cpu_count", lambda: 2)
    monkeypatch.setattr(probes, "_read_text",
                        lambda path, limit=65536: CPUINFO_ARM if "cpuinfo" in str(path) else "")
    monkeypatch.setattr(probes.shutil, "which", lambda name: None)
    result = probes.probe_cpu(settings)
    assert result["data"]["model"] == "Neoverse-N1"
    assert result["data"]["arch"] == "aarch64"


# ──────────────────────────────────────────────────────────────────── tools


def _fake_which(mapping):
    return lambda name: mapping.get(name)


def test_tools_versions_are_parsed_and_install_paths_discarded(monkeypatch, settings):
    mapping = {"python3": "/venv/bin/python3", "git": "/usr/bin/git", "pip": "/venv/bin/pip"}
    monkeypatch.setattr(probes.shutil, "which", _fake_which(mapping))

    def fake_run(argv, timeout=1.5):
        first = str(argv[0])
        if first.endswith("python3"):
            return True, "Python 3.12.4"
        if first.endswith("git"):
            return True, "git version 2.43.0"
        if first.endswith("pip"):
            return True, "pip 24.0 from /home/alice/venv/lib/python3.12/site-packages/pip (python 3.12)"
        return False, ""

    monkeypatch.setattr(probes, "_run", fake_run)
    result = probes.probe_tools(settings)
    items = result["data"]["items"]
    assert items["python3"] == {"present": True, "version": "3.12.4", "source": "python3 -V"}
    assert items["git"]["version"] == "2.43.0"
    assert items["pip"]["version"] == "24.0"
    flattened = repr(result)
    assert "/home/alice" not in flattened and "site-packages" not in flattened
    assert items["jq"]["present"] is False


def test_tools_reports_browsers_and_an_absent_docker_binary(monkeypatch, settings):
    monkeypatch.setattr(probes.shutil, "which", _fake_which({"chromium": "/usr/bin/chromium"}))
    monkeypatch.setattr(probes, "_run", lambda argv, timeout=1.5: (True, "1.2.3"))
    result = probes.probe_tools(settings)
    data = result["data"]
    assert data["browsers"]["chromium"]["present"] is True
    assert data["browsers"]["google-chrome"]["present"] is False
    assert data["docker"]["binary"] is False
    assert data["docker"]["daemon"]["status"] == "absent"


def test_docker_daemon_distinguishes_running_refused_and_forbidden(monkeypatch, settings):
    monkeypatch.setattr(probes.shutil, "which", _fake_which({"docker": "/usr/bin/docker"}))

    def ok(argv, timeout=1.5):
        return True, "27.0.3\n"

    monkeypatch.setattr(probes, "_run", ok)
    assert probes.probe_tools(settings)["data"]["docker"]["daemon"]["status"] == "reachable"

    monkeypatch.setattr(
        probes, "_run",
        lambda argv, timeout=1.5: (False, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"))
    daemon = probes.probe_tools(settings)["data"]["docker"]["daemon"]
    assert daemon["status"] == "unreachable"
    assert "docker.sock" not in repr(daemon), "raw daemon stderr must not be cached"

    monkeypatch.setattr(
        probes, "_run",
        lambda argv, timeout=1.5: (False, "permission denied while trying to connect to the Docker daemon socket"))
    assert probes.probe_tools(settings)["data"]["docker"]["daemon"]["status"] == "unknown"


def test_tool_version_probing_respects_a_total_budget(monkeypatch, settings):
    clock = {"now": 0.0}
    monkeypatch.setattr(probes, "_clock", lambda: clock["now"])

    def slow_run(argv, timeout=1.5):
        clock["now"] += 5.0
        return True, "1.2.3"

    monkeypatch.setattr(probes, "_run", slow_run)
    monkeypatch.setattr(probes.shutil, "which", lambda name: "/usr/bin/" + name)
    result = probes.probe_tools(settings)
    items = result["data"]["items"]
    probed = [name for name, item in items.items() if item["version"]]
    assert 0 < len(probed) < len(settings.tools)
    assert any("time budget" in line for line in result["evidence"])
    assert all("version not probed" in item["source"]
               for name, item in items.items() if name not in probed)


# ────────────────────────────────────────────────────────────────────── gpu

NV_SMI = "NVIDIA GeForce RTX 4090, 24564\nNVIDIA GeForce RTX 4090, 24564\n"
LSPCI_VIRTUAL = (
    "00:00.0 Host bridge: Red Hat, Inc. QEMU PCIe Host bridge\n"
    "00:01.0 Display controller: Red Hat, Inc. Virtio 1.0 GPU (rev 01)\n"
)
LSPCI_NVIDIA = "01:00.0 VGA compatible controller: NVIDIA Corporation GA102 [GeForce RTX 3090] (rev a1)\n"
LSPCI_NONE = "00:00.0 Host bridge: Intel Corporation Device 1234\n"


def test_gpu_uses_nvidia_smi_when_the_driver_answers(monkeypatch, settings):
    monkeypatch.setattr(probes.shutil, "which", _fake_which({"nvidia-smi": "/usr/bin/nvidia-smi"}))
    calls: list[list[str]] = []

    def fake_run(argv, timeout=1.5):
        calls.append(list(argv))
        return True, NV_SMI

    monkeypatch.setattr(probes, "_run", fake_run)
    result = probes.probe_gpu(settings)
    assert result["data"]["vendor"] == "nvidia"
    assert result["data"]["compute"] is True
    assert result["data"]["vram_mb"] == 24564
    assert result["data"]["devices"][0]["name"] == "NVIDIA GeForce RTX 4090"
    assert calls and "lspci" not in calls[0][0]


def test_gpu_calls_a_virtual_adapter_what_it_is(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")
    monkeypatch.setattr(probes.shutil, "which", _fake_which({"lspci": "/usr/bin/lspci"}))
    monkeypatch.setattr(probes, "_run", lambda argv, timeout=1.5: (True, LSPCI_VIRTUAL))
    result = probes.probe_gpu(settings)
    assert result["data"]["vendor"] == "virtual"
    assert result["data"]["compute"] is False
    assert result["data"]["devices"][0]["name"] == "Red Hat, Inc. Virtio 1.0 GPU"


def test_gpu_without_driver_reports_no_cuda_even_if_the_card_is_visible(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")
    monkeypatch.setattr(probes.shutil, "which", _fake_which({"lspci": "/usr/bin/lspci"}))
    monkeypatch.setattr(probes, "_run", lambda argv, timeout=1.5: (True, LSPCI_NVIDIA))
    result = probes.probe_gpu(settings)
    assert result["data"]["vendor"] == "nvidia"
    assert result["data"]["compute"] is False
    assert "nvidia-smi is absent" in result["data"]["note"]


def test_gpu_absent_listing_is_none_not_unknown(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")
    monkeypatch.setattr(probes.shutil, "which", _fake_which({"lspci": "/usr/bin/lspci"}))
    monkeypatch.setattr(probes, "_run", lambda argv, timeout=1.5: (True, LSPCI_NONE))
    result = probes.probe_gpu(settings)
    assert result["data"]["vendor"] == "none"
    assert result["data"]["compute"] is False


def test_gpu_without_any_probe_tool_is_unknown(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "FreeBSD")
    monkeypatch.setattr(probes.shutil, "which", lambda name: None)
    result = probes.probe_gpu(settings)
    assert result["data"]["vendor"] == "unknown"
    assert result["status"] == "unknown"


# ─────────────────────────────────────────────────────── system and failures


def test_system_detects_systemd_container_and_privileges(monkeypatch, settings):
    monkeypatch.setattr(probes._platform, "system", lambda: "Linux")

    def fake_read(path, limit=65536):
        path = str(path)
        if path == "/proc/1/comm":
            return "systemd\n"
        if path == "/proc/1/cgroup":
            return "0::/docker/abc\n"
        return ""

    monkeypatch.setattr(probes, "_read_text", fake_read)
    monkeypatch.setattr(probes, "_exists", lambda path: str(path) == "/.dockerenv")
    monkeypatch.setattr(probes.shutil, "which", _fake_which({"bash": "/bin/bash"}))
    result = probes.probe_system(settings)
    assert result["data"]["init"] == "systemd"
    assert result["data"]["container"] == "docker"
    assert result["data"]["shells"] == ["bash"]
    assert result["data"]["elevated"] in (True, False)


def test_disk_probe_never_prints_the_path(monkeypatch, hermes_home, settings):
    result = probes.probe_disk(settings)
    assert result["status"] == "measured"
    assert result["data"]["free_bytes"] > 0
    assert str(hermes_home) not in repr(result)


def test_a_failing_probe_becomes_unknown_and_does_not_stop_the_others(monkeypatch, hermes_home, settings):
    def boom(_settings):
        raise RuntimeError("meminfo exploded at /home/alice")

    monkeypatch.setitem(probes._PROBES, "memory", boom)
    facts = probes.measure(settings, use_cache=False)
    assert facts["sections"]["memory"]["status"] == "unknown"
    assert facts["sections"]["cpu"]["status"] in ("measured", "partial")
    assert "alice" not in repr(facts["sections"]["memory"])


def test_measure_covers_every_section_even_when_everything_fails(monkeypatch, hermes_home, settings):
    def boom(_settings):
        raise OSError("no")

    for name in probes.SECTIONS:
        monkeypatch.setitem(probes._PROBES, name, boom)
    facts = probes.measure(settings, use_cache=False)
    assert set(facts["sections"]) == set(probes.SECTIONS)
    assert all(entry["status"] == "unknown" for entry in facts["sections"].values())


def test_version_tokens_come_from_banners_not_free_text():
    assert probes._version_of("Docker version 27.0.3, build abc") == "27.0.3"
    assert probes._version_of("Homebrew 4.3.0") == "4.3.0"
    assert probes._version_of("no numbers here") is None
    assert probes._version_of("") is None


def test_clean_name_drops_anything_path_like():
    assert probes._clean_name("NVIDIA GeForce RTX 4090") == "NVIDIA GeForce RTX 4090"
    assert probes._clean_name("/home/alice/gpu") == ""
    assert probes._clean_name("a@b") == ""
