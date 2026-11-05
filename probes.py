"""Measurement probes — the only place ``specs`` touches the outside world.

Every probe answers one question about the machine, records *how* it found the
answer (the evidence list each section carries) and fails soft: a probe that
raises, times out or is unreadable becomes ``status: "unknown"`` and never stops
the others. Nothing here passes a shell string to a shell, starts a daemon,
reads a credential file or looks at environment-variable values; subprocesses are
run with an argv list and a hard timeout.

The cache lives at ``<HERMES_HOME>/plugin-data/specs/specs.json``. It is written
atomically, validated on read, and a corrupt file is treated exactly like a
missing one (live probe, then rewrite).
"""

from __future__ import annotations

import json
import os
import platform as _platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .settings import PLUGIN_ID, DEFAULT_BROWSERS, Settings

SCHEMA_VERSION = 1
SECTIONS = ("system", "cpu", "memory", "gpu", "disk", "tools")

#: Probe status vocabulary. ``partial`` means some facts in the section were
#: measured and others were not; it is still safe to quote the measured ones.
STATUS_MEASURED = "measured"
STATUS_PARTIAL = "partial"
STATUS_UNKNOWN = "unknown"

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-+~][0-9A-Za-z.]+)?)")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

#: Indirection so tests can drive the tools-probe budget without touching the clock
#: every other module in the process shares.
_clock = time.monotonic

#: ``/proc/cpuinfo`` on ARM says which core it is by part number.
_ARM_PARTS = {
    "0xd0c": "Neoverse-N1",
    "0xd40": "Neoverse-V1",
    "0xd49": "Neoverse-N2",
    "0xd4f": "Neoverse-V2",
    "0xd44": "Cortex-A76",
    "0xd46": "Cortex-A510",
    "0xd47": "Cortex-A710",
    "0xd4d": "Cortex-A715",
    "0xd48": "Cortex-X1",
    "0xd4e": "Cortex-X2",
    "0xd0b": "Cortex-A72",
}

_TOOL_ARGV: dict[str, tuple[str, ...]] = {
    "python3": ("-V",),
    "pip": ("--version",),
    "uv": ("--version",),
    "node": ("--version",),
    "npm": ("--version",),
    "git": ("--version",),
    "ffmpeg": ("-version",),
    "curl": ("--version",),
    "jq": ("--version",),
    "sqlite3": ("--version",),
    "tesseract": ("--version",),
    "docker": ("--version",),
    "podman": ("--version",),
    "systemctl": ("--version",),
    "apt": ("--version",),
    "dnf": ("--version",),
    "pacman": ("--version",),
    "brew": ("--version",),
    "winget": ("--version",),
}


# ─────────────────────────────────────────────────────────────── primitives


def hermes_home() -> Path:
    """Resolve the Hermes home the way core does, with an env fallback."""
    try:
        from hermes_constants import get_hermes_home  # public plugin surface

        return Path(get_hermes_home())
    except Exception:
        raw = os.environ.get("HERMES_HOME", "").strip()
        if raw:
            return Path(os.path.expanduser(os.path.expandvars(raw)))
        return Path.home() / ".hermes"


def _read_text(path: str | Path, limit: int = 65536) -> str:
    """Read a small system file; returns '' rather than raising when it is absent."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(limit)
        return raw.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def _run(argv: Sequence[str], timeout: float = 1.5) -> tuple[bool, str]:
    """Run an argv list with a hard timeout. Returns ``(ok, text)``, never raises.

    ``text`` is stdout when it has content, otherwise stderr — several of the
    tools we version (``tesseract``, ``systemctl``) write their banner to stderr.
    Output is capped so a chatty binary cannot bloat the cache.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell, fixed timeout
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=max(0.05, float(timeout)),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False, ""
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    text = out or err
    return proc.returncode == 0, text[:4000]


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _version_of(text: str) -> str | None:
    """Pull a bare version token out of a banner.

    A version is the only thing we keep: banners routinely embed install paths
    (``pip 25.0 from /home/.../site-packages``), and paths are exactly what this
    plugin promises not to cache.
    """
    match = _VERSION_RE.search(_first_line(text))
    return match.group(1) if match else None


def _clean_name(raw: str, limit: int = 64) -> str:
    """Whitespace-collapse a device/model name and drop anything path-like."""
    text = _CONTROL_RE.sub(" ", str(raw or "")).strip()
    text = re.sub(r"\s+", " ", text)
    if "@" in text or "/" in text or "\\" in text:
        return ""
    return text[:limit]


def _exists(path: str | Path) -> bool:
    try:
        return Path(path).exists()
    except (OSError, ValueError):
        return False


def _meminfo_kb(text: str, key: str) -> int | None:
    match = re.search(rf"^{re.escape(key)}:\s*(\d+)\s*kB", text or "", re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


# ─────────────────────────────────────────────────────────────────── probes


def probe_system(settings: Settings) -> dict[str, Any]:
    """OS, kernel, arch, init system, container status, privileges, shells."""
    evidence: list[str] = []
    data: dict[str, Any] = {}
    partial = False

    try:
        data["os"] = _platform.system() or "unknown"
        data["kernel"] = _clean_name(_platform.release(), 48) or "unknown"
        data["arch"] = _clean_name(_platform.machine(), 24) or "unknown"
        data["python"] = _platform.python_version()
        evidence.append("platform.system(), platform.release(), platform.machine()")
    except Exception:
        partial = True

    data["init"] = "unknown"
    data["container"] = None
    if data.get("os") == "Linux":
        pid1 = _read_text("/proc/1/comm").strip()
        evidence.append("/proc/1/comm")
        if pid1:
            data["init"] = _clean_name(pid1, 24) or "unknown"
        markers = [name for name in (".dockerenv",) if _exists("/" + name)]
        if _exists("/run/.containerenv"):
            markers.append("containerenv")
        cgroup = _read_text("/proc/1/cgroup")
        lowered = cgroup.lower()
        for token in ("docker", "kubepods", "containerd", "lxc", "podman"):
            if token in lowered:
                markers.append(token)
        if _exists("/proc/vz") and not _exists("/proc/bc"):
            markers.append("openvz")
        if markers:
            first = markers[0]
            data["container"] = "docker" if first == ".dockerenv" else (
                "podman" if first == "containerenv" else first)
        evidence.append("/.dockerenv, /run/.containerenv, /proc/1/cgroup")
    elif data.get("os") == "Darwin":
        data["init"] = "launchd"
        evidence.append("platform.system() == Darwin (launchd is PID 1 by definition)")
    elif data.get("os") == "Windows":
        data["init"] = "unknown"
        evidence.append("PID 1 is not a Windows concept")

    try:
        if hasattr(os, "geteuid"):
            data["elevated"] = os.geteuid() == 0
            evidence.append("os.geteuid()")
        else:
            data["elevated"] = None
    except Exception:
        data["elevated"] = None

    shells = [name for name in ("bash", "zsh", "fish", "sh", "pwsh") if shutil.which(name)]
    data["shells"] = shells
    evidence.append("shutil.which() over bash/zsh/fish/sh/pwsh")

    status = STATUS_PARTIAL if partial else STATUS_MEASURED
    if not data.get("os"):
        status = STATUS_UNKNOWN
    return {"status": status, "data": data, "evidence": evidence}


def probe_cpu(settings: Settings) -> dict[str, Any]:
    """Logical core count and a human model name."""
    evidence: list[str] = []
    data: dict[str, Any] = {"cores": None, "model": "unknown"}

    try:
        data["cores"] = os.cpu_count()
        evidence.append("os.cpu_count()")
    except Exception:
        pass

    model = ""
    arch = _clean_name(_platform.machine(), 24)
    data["arch"] = arch or "unknown"

    cpuinfo = _read_text("/proc/cpuinfo")
    if cpuinfo:
        evidence.append("/proc/cpuinfo")
        for key in ("model name", "Model", "Hardware", "Processor", "cpu model", "uarch"):
            match = re.search(rf"^{re.escape(key)}\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
            if match:
                model = _clean_name(match.group(1), 64)
                if model:
                    break
        if not model:
            part = re.search(r"^CPU part\s*:\s*(0x[0-9a-fA-F]+)", cpuinfo, re.MULTILINE)
            if part:
                code = part.group(1).lower()
                model = _ARM_PARTS.get(code) or f"ARMv8 (part {code})"
        if data["cores"] is None:
            count = len(re.findall(r"^processor\s*:", cpuinfo, re.MULTILINE))
            if count:
                data["cores"] = count

    if not model and shutil.which("lscpu"):
        ok, out = _run(["lscpu"], timeout=settings.timeout_seconds)
        if ok:
            evidence.append("lscpu (Model name)")
            match = re.search(r"^Model name:\s*(.+)$", out, re.MULTILINE)
            if match:
                model = _clean_name(match.group(1), 64)

    if not model and _platform.system() != "Linux":
        model = _clean_name(_platform.processor(), 64)
        if model:
            evidence.append("platform.processor()")

    if model:
        data["model"] = model
    status = STATUS_MEASURED if data["cores"] and model else (
        STATUS_PARTIAL if data["cores"] or model else STATUS_UNKNOWN
    )
    return {"status": status, "data": data, "evidence": evidence}


def probe_memory(settings: Settings) -> dict[str, Any]:
    """Total and *available* RAM (available is what feasibility arithmetic uses)."""
    system = _platform.system()
    evidence: list[str] = []
    data: dict[str, Any] = {"total_bytes": None, "available_bytes": None, "estimate": False}

    if system == "Linux":
        text = _read_text("/proc/meminfo")
        total = _meminfo_kb(text, "MemTotal")
        available = _meminfo_kb(text, "MemAvailable")
        evidence.append("/proc/meminfo MemTotal")
        if available is None:
            free = _meminfo_kb(text, "MemFree")
            if free is not None:
                available = free + (_meminfo_kb(text, "Buffers") or 0) + (_meminfo_kb(text, "Cached") or 0)
                data["estimate"] = True
            evidence.append("/proc/meminfo MemFree+Buffers+Cached")
        else:
            evidence.append("/proc/meminfo MemAvailable")
        data["total_bytes"] = total
        data["available_bytes"] = available
    elif system == "Darwin":
        ok, out = _run(["sysctl", "-n", "hw.memsize"], timeout=max(settings.timeout_seconds, 2.0))
        evidence.append("sysctl -n hw.memsize")
        if ok:
            try:
                data["total_bytes"] = int(_first_line(out))
            except ValueError:
                pass
        ok, out = _run(["vm_stat"], timeout=max(settings.timeout_seconds, 2.0))
        evidence.append("vm_stat (free + inactive + speculative pages)")
        if ok:
            page = re.search(r"page size of (\d+) bytes", out)
            size = int(page.group(1)) if page else 4096
            pages = 0
            for label in ("Pages free", "Pages inactive", "Pages speculative"):
                match = re.search(rf"^{label}:\s*(\d+)", out, re.MULTILINE)
                if match:
                    pages += int(match.group(1))
            if pages:
                data["available_bytes"] = pages * size
    elif system == "Windows":
        try:
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                data["total_bytes"] = int(status.ullTotalPhys)
                data["available_bytes"] = int(status.ullAvailPhys)
                evidence.append("ctypes GlobalMemoryStatusEx")
        except Exception:
            pass

    total, available = data["total_bytes"], data["available_bytes"]
    status = STATUS_MEASURED if total and available else (STATUS_PARTIAL if total else STATUS_UNKNOWN)
    return {"status": status, "data": data, "evidence": evidence}


def _gpu_from_nvidia(settings: Settings, evidence: list[str]) -> dict[str, Any] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    ok, out = _run(
        [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        timeout=max(settings.timeout_seconds, 2.0),
    )
    evidence.append("nvidia-smi --query-gpu=name,memory.total")
    if not ok or not out:
        return None
    devices: list[dict[str, Any]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, _, mem = line.rpartition(",")
        if not name:
            name, mem = line, ""
        label = _clean_name(name, 64) or "NVIDIA GPU"
        vram_mb: int | None = None
        try:
            vram_mb = int(float(mem.strip()))
        except (TypeError, ValueError):
            pass
        devices.append({"name": label, "vram_mb": vram_mb})
    if not devices:
        return None
    vram = max((d["vram_mb"] or 0) for d in devices) or None
    return {
        "vendor": "nvidia",
        "devices": devices,
        "vram_mb": vram,
        "compute": True,
        "note": "CUDA available through the installed NVIDIA driver",
    }


def _gpu_from_rocm(settings: Settings, evidence: list[str]) -> dict[str, Any] | None:
    exe = shutil.which("rocm-smi")
    if not exe:
        return None
    ok, out = _run([exe, "--showproductname"], timeout=max(settings.timeout_seconds, 3.0))
    evidence.append("rocm-smi --showproductname")
    if not ok:
        return None
    names = []
    for line in out.splitlines():
        if "card series" in line.lower() or "card model" in line.lower():
            _, _, value = line.partition(":")
            label = _clean_name(value, 64)
            if label and label not in names:
                names.append(label)
    if not names:
        return None
    return {
        "vendor": "amd",
        "devices": [{"name": name, "vram_mb": None} for name in names[:4]],
        "vram_mb": None,
        "compute": True,
        "note": "ROCm device present; VRAM size was not readable, check rocm-smi --showmeminfo",
    }


def _gpu_from_lspci(settings: Settings, evidence: list[str]) -> dict[str, Any] | None:
    exe = shutil.which("lspci")
    if not exe:
        return None
    ok, out = _run([exe], timeout=max(settings.timeout_seconds, 2.0))
    evidence.append("lspci (VGA/3D/Display controller lines)")
    if not ok:
        return None
    found: list[dict[str, Any]] = []
    for line in out.splitlines():
        lowered = line.lower()
        if not any(kind in lowered for kind in ("vga compatible", "3d controller", "display controller")):
            continue
        name = line.split("controller:", 1)[-1].strip() if "controller:" in line else line
        name = re.sub(r"\s*\(rev [0-9a-f]+\)\s*$", "", name, flags=re.IGNORECASE)
        label = _clean_name(name, 64)
        if label:
            found.append({"name": label, "vram_mb": None})
    if not found:
        return {"vendor": "none", "devices": [], "vram_mb": None, "compute": False,
                "note": "no display controller reported by lspci"}
    joined = " ".join(d["name"].lower() for d in found)
    virtual = ("virtio", "qemu", "virtual", "vmware", "cirrus", "bochs", "hyper-v", "parallels")
    if any(token in joined for token in virtual):
        return {"vendor": "virtual", "devices": found[:4], "vram_mb": None, "compute": False,
                "note": "virtual display adapter: no CUDA/ROCm compute and no usable VRAM"}
    if "nvidia" in joined:
        return {"vendor": "nvidia", "devices": found[:4], "vram_mb": None, "compute": False,
                "note": "NVIDIA GPU visible on the bus but nvidia-smi is absent: the driver is not usable"}
    if "amd" in joined or "advanced micro devices" in joined or "ati " in joined:
        return {"vendor": "amd", "devices": found[:4], "vram_mb": None, "compute": None,
                "note": "AMD GPU visible on the bus but rocm-smi is absent: ROCm status unknown"}
    return {"vendor": "other", "devices": found[:4], "vram_mb": None, "compute": None,
            "note": "display adapter present; compute capability not established"}


def probe_gpu(settings: Settings) -> dict[str, Any]:
    """Detect a *compute-capable* GPU, not merely a display adapter."""
    evidence: list[str] = []
    if not settings.gpu_probe:
        return {
            "status": STATUS_UNKNOWN,
            "data": {"vendor": "unknown", "devices": [], "vram_mb": None, "compute": None},
            "evidence": ["gpu_probe is disabled in settings"],
        }

    for finder in (_gpu_from_nvidia, _gpu_from_rocm):
        found = finder(settings, evidence)
        if found:
            return {"status": STATUS_MEASURED, "data": found, "evidence": evidence}

    system = _platform.system()
    if system == "Darwin":
        try:
            if _platform.machine() == "arm64":
                evidence.append("platform.machine() == arm64 (Apple silicon: unified memory + Metal)")
                return {
                    "status": STATUS_MEASURED,
                    "data": {"vendor": "apple", "devices": [{"name": "Apple integrated GPU", "vram_mb": None}],
                             "vram_mb": None, "compute": True,
                             "note": "Metal compute; GPU memory is shared with system RAM"},
                    "evidence": evidence,
                }
            ok, out = _run(["system_profiler", "SPDisplaysDataType"], timeout=max(settings.timeout_seconds, 4.0))
            evidence.append("system_profiler SPDisplaysDataType")
            if ok:
                chips = [_clean_name(m.group(1), 64)
                         for m in re.finditer(r"Chipset Model:\s*(.+)$", out, re.MULTILINE)]
                chips = [c for c in chips if c]
                if chips:
                    return {
                        "status": STATUS_MEASURED,
                        "data": {"vendor": "other", "devices": [{"name": c, "vram_mb": None} for c in chips[:4]],
                                 "vram_mb": None, "compute": None,
                                 "note": "discrete/integrated display GPU; vendor compute stack not probed"},
                        "evidence": evidence,
                    }
        except Exception:
            pass
    elif system == "Windows":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        ok, out = False, ""
        if shell:
            ok, out = _run(
                [shell, "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                timeout=max(settings.timeout_seconds, 5.0),
            )
        evidence.append("powershell Get-CimInstance Win32_VideoController")
        if ok and out.strip():
            names = [_clean_name(line, 64) for line in out.splitlines() if _clean_name(line, 64)]
            if names:
                return {
                    "status": STATUS_MEASURED,
                    "data": {"vendor": "other", "devices": [{"name": n, "vram_mb": None} for n in names[:4]],
                             "vram_mb": None, "compute": None,
                             "note": "display adapter(s) reported by Windows; vendor compute stack not probed"},
                    "evidence": evidence,
                }

    found = _gpu_from_lspci(settings, evidence)
    if found:
        status = STATUS_MEASURED if found.get("compute") is not None else STATUS_PARTIAL
        return {"status": status, "data": found, "evidence": evidence}

    return {
        "status": STATUS_UNKNOWN,
        "data": {"vendor": "unknown", "devices": [], "vram_mb": None, "compute": None},
        "evidence": evidence or ["no GPU probe tool available (nvidia-smi, rocm-smi, lspci)"],
    }


def probe_disk(settings: Settings) -> dict[str, Any]:
    """Free space on the volume that holds plugin state (not a user path)."""
    start = hermes_home()
    candidate = start
    for _ in range(6):
        if _exists(candidate):
            break
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    try:
        usage = shutil.disk_usage(str(candidate))
    except (OSError, ValueError):
        return {
            "status": STATUS_UNKNOWN,
            "data": {"free_bytes": None, "total_bytes": None, "label": "hermes data volume"},
            "evidence": ["shutil.disk_usage() failed"],
        }
    return {
        "status": STATUS_MEASURED,
        "data": {
            "free_bytes": int(usage.free),
            "total_bytes": int(usage.total),
            "label": "hermes data volume",
        },
        "evidence": ["shutil.disk_usage() on the volume holding the Hermes home"],
    }


def _docker_daemon(settings: Settings) -> dict[str, Any]:
    exe = shutil.which("docker")
    source = "docker info --format {{.ServerVersion}}"
    if not exe:
        return {"status": "absent", "server": None, "source": "shutil.which('docker')"}
    ok, out = _run([exe, "info", "--format", "{{.ServerVersion}}"], timeout=max(settings.timeout_seconds, 2.5))
    if ok and out.strip():
        return {"status": "reachable", "server": _clean_name(_first_line(out), 32) or None, "source": source}
    lowered = out.lower()
    if "permission denied" in lowered:
        return {"status": "unknown", "server": None,
                "source": source, "reason": "the daemon socket is not readable by this user"}
    if "cannot connect" in lowered or "is the docker daemon running" in lowered or "connection refused" in lowered:
        return {"status": "unreachable", "server": None,
                "source": source, "reason": "no daemon is answering"}
    return {"status": "unknown", "server": None, "source": source,
            "reason": "docker info did not answer in time"}


def probe_tools(settings: Settings) -> dict[str, Any]:
    """Presence and version for every configured tool, plus browsers and the daemon.

    Version banners are the slow part (one subprocess each), so the loop runs under
    a total budget: a machine with many installed-but-hung tools costs seconds, not
    a stalled session start. Tools past the budget keep their presence fact and are
    recorded as "version not probed".
    """
    evidence: list[str] = []
    items: dict[str, Any] = {}
    seen_versions = False
    budget = min(10.0, max(2.0, settings.timeout_seconds * 4))
    deadline = _clock() + budget
    truncated = False
    for name in settings.tools:
        argv = _TOOL_ARGV.get(name, ("--version",))
        exe = shutil.which(name)
        if not exe:
            items[name] = {"present": False, "version": None,
                           "source": f"shutil.which({name!r}) found nothing"}
            continue
        remaining = deadline - _clock()
        if remaining < 0.05:
            truncated = True
            items[name] = {"present": True, "version": None,
                           "source": "present; version not probed (time budget reached)"}
            continue
        ok, out = _run([exe, *argv], timeout=min(settings.timeout_seconds, remaining))
        version = _version_of(out) if ok else None
        seen_versions = seen_versions or bool(version)
        items[name] = {"present": True, "version": version, "source": f"{name} {' '.join(argv)}"}
    evidence.append(
        "shutil.which() for presence, then `<tool> --version` with a "
        f"{settings.probe_timeout_ms}ms per-tool timeout ({budget:.1f}s total budget) for the version string"
    )
    if truncated:
        evidence.append("the version time budget ran out: later tools are present with version unprobed")
    if not seen_versions:
        evidence.append("no version banner parsed: tools are reported as present without a version")

    browsers = {}
    for name in DEFAULT_BROWSERS:
        browsers[name] = {"present": shutil.which(name) is not None,
                          "source": f"shutil.which({name!r})"}

    docker = {"binary": bool(items.get("docker", {}).get("present")), "daemon": None}
    if settings.docker_probe:
        docker["daemon"] = _docker_daemon(settings)
        evidence.append("docker info --format {{.ServerVersion}} (read-only: never starts a daemon)")
    else:
        docker["daemon"] = {"status": "unknown", "source": "docker_probe is disabled in settings"}

    measured = sum(1 for item in items.values() if item["present"])
    status = STATUS_MEASURED if items else STATUS_UNKNOWN
    return {
        "status": status,
        "data": {"items": items, "browsers": browsers, "docker": docker,
                 "present_count": measured, "measured_count": len(items)},
        "evidence": evidence,
    }


_PROBES: dict[str, Callable[[Settings], dict[str, Any]]] = {
    "system": probe_system,
    "cpu": probe_cpu,
    "memory": probe_memory,
    "gpu": probe_gpu,
    "disk": probe_disk,
    "tools": probe_tools,
}


# ──────────────────────────────────────────────────────────────────── cache


def plugin_data_dir() -> Path:
    return hermes_home() / "plugin-data" / PLUGIN_ID


def cache_path() -> Path:
    return plugin_data_dir() / "specs.json"


def _normalise_section(name: str, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    data = raw.get("data")
    if not isinstance(data, dict):
        return None
    try:
        epoch = float(raw.get("measured_epoch"))
    except (TypeError, ValueError):
        return None
    status = str(raw.get("status") or STATUS_UNKNOWN)
    if status not in (STATUS_MEASURED, STATUS_PARTIAL, STATUS_UNKNOWN):
        status = STATUS_UNKNOWN
    evidence = [str(item)[:200] for item in (raw.get("evidence") or []) if isinstance(item, (str, int))]
    return {"status": status, "data": data, "evidence": evidence, "measured_epoch": epoch}


def load_cache(path: Path | str | None = None) -> dict[str, Any] | None:
    """Load and validate the cache. Anything unexpected returns None (= re-probe)."""
    target = Path(path) if path is not None else cache_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("schema") != SCHEMA_VERSION:
        return None
    sections_raw = parsed.get("sections")
    if not isinstance(sections_raw, dict):
        return None
    sections: dict[str, Any] = {}
    for name in SECTIONS:
        entry = _normalise_section(name, sections_raw.get(name))
        if entry is not None:
            sections[name] = entry
    if not sections:
        return None
    return {"schema": SCHEMA_VERSION, "sections": sections}


def save_cache(facts: Mapping[str, Any], path: Path | str | None = None) -> bool:
    """Write the cache atomically; a filesystem error is reported, never raised."""
    target = Path(path) if path is not None else cache_path()
    payload = {
        "schema": SCHEMA_VERSION,
        "plugin": PLUGIN_ID,
        "sections": facts.get("sections") or {},
    }
    tmp = target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except (OSError, ValueError, TypeError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _safe_probe(name: str, settings: Settings) -> dict[str, Any]:
    """Run one probe behind every guard we have. Never raises."""
    started = time.time()
    try:
        result = _PROBES[name](settings)
        if not isinstance(result, dict):
            raise TypeError("probe returned a non-dict")
    except Exception as exc:  # a probe must never break the caller
        return {
            "status": STATUS_UNKNOWN,
            "data": {},
            "evidence": [],
            "error": type(exc).__name__,
            "measured_epoch": started,
        }
    entry = {
        "status": str(result.get("status") or STATUS_MEASURED),
        "data": result.get("data") if isinstance(result.get("data"), dict) else {},
        "evidence": [str(item)[:200] for item in (result.get("evidence") or [])],
        "measured_epoch": time.time(),
    }
    if entry["status"] not in (STATUS_MEASURED, STATUS_PARTIAL, STATUS_UNKNOWN):
        entry["status"] = STATUS_UNKNOWN
    if result.get("error"):
        entry["error"] = str(result["error"])[:120]
    return entry


def section_age(facts: Mapping[str, Any], name: str) -> float | None:
    entry = (facts.get("sections") or {}).get(name)
    if not isinstance(entry, dict):
        return None
    try:
        return max(0.0, time.time() - float(entry.get("measured_epoch")))
    except (TypeError, ValueError):
        return None


def measured_epoch(facts: Mapping[str, Any]) -> float:
    """When the *oldest* section in the cache was measured."""
    stamps = []
    for entry in (facts.get("sections") or {}).values():
        if isinstance(entry, dict):
            try:
                stamps.append(float(entry.get("measured_epoch")))
            except (TypeError, ValueError):
                continue
    return min(stamps) if stamps else time.time()


def is_stale(facts: Mapping[str, Any] | None, settings: Settings) -> bool:
    if not facts:
        return True
    ttl = settings.ttl_seconds
    for name in SECTIONS:
        age = section_age(facts, name)
        if age is None or age > ttl:
            return True
    return False


def measure(
    settings: Settings | None = None,
    *,
    force: Iterable[str] = (),
    path: Path | str | None = None,
    use_cache: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    """Measure the machine, reusing fresh cache sections and re-probing stale ones.

    ``force`` names sections to re-probe regardless of age (``"all"`` = every
    section). ``use_cache=False`` ignores what is on disk and measures everything;
    ``persist=False`` keeps the result out of the cache file (tests and previews).
    The returned dict is always complete: every section is present, with
    ``status: "unknown"`` when its probe could not answer.
    """
    settings = settings or Settings()
    forced = {str(name) for name in force}
    if "all" in forced or "*" in forced:
        forced = set(SECTIONS)

    cached = load_cache(path) if use_cache else None
    sections: dict[str, Any] = dict((cached or {}).get("sections") or {})
    ttl = settings.ttl_seconds

    for name in SECTIONS:
        entry = sections.get(name)
        fresh = False
        if entry is not None and name not in forced:
            age = section_age({"sections": {name: entry}}, name)
            fresh = age is not None and age <= ttl
        if fresh:
            continue
        sections[name] = _safe_probe(name, settings)

    facts = {"schema": SCHEMA_VERSION, "sections": sections}
    if persist:
        save_cache(facts, path)
    return facts
