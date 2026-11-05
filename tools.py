"""Tool handlers: ``machine_specs``, ``machine_can`` and ``machine_refresh``.

Contract, applied literally: ``def handler(args: dict, **kwargs) -> str`` and no
exception ever leaves a handler. A tool that cannot answer says so in text; it
does not raise into the turn.

``machine_can`` is the interesting one. It is a *heuristic*: it parses the task
for the thing being attempted, compares it with the measured facts, and prints
the arithmetic it used — including the assumptions — so a wrong verdict is
arguable instead of mysterious.
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable, Mapping, Sequence

from . import card as _card
from . import probes
from .settings import Settings

HEURISTIC_NOTE = "heuristic verdict, not a guarantee — the arithmetic is shown so you can check it"


def get_settings() -> Settings:
    from . import current_settings

    return current_settings()


# ─────────────────────────────────────────────────────────── fact formatting


def _section(facts: Mapping[str, Any], name: str) -> dict[str, Any]:
    entry = (facts.get("sections") or {}).get(name)
    if not isinstance(entry, Mapping):
        return {}
    data = entry.get("data")
    return dict(data) if isinstance(data, Mapping) else {}


def _evidence(facts: Mapping[str, Any], name: str) -> str:
    entry = (facts.get("sections") or {}).get(name)
    if not isinstance(entry, Mapping):
        return "not measured"
    evidence = entry.get("evidence") or []
    return "; ".join(str(item) for item in evidence) or "no evidence recorded"


def _status(facts: Mapping[str, Any], name: str) -> str:
    entry = (facts.get("sections") or {}).get(name)
    return str(entry.get("status") or "unknown") if isinstance(entry, Mapping) else "unknown"


def _stamp(facts: Mapping[str, Any], name: str) -> str:
    entry = (facts.get("sections") or {}).get(name)
    try:
        epoch = float(entry.get("measured_epoch"))
    except (TypeError, ValueError, AttributeError):
        return "unknown time"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(epoch))


def _age(facts: Mapping[str, Any]) -> str:
    epoch = probes.measured_epoch(facts)
    seconds = max(0.0, time.time() - epoch)
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


def _gb(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    try:
        return f"{float(value) / 1e9:.2f} GB"
    except (TypeError, ValueError):
        return "unknown"


def _with_evidence(label: str, value: str, evidence: str) -> str:
    return f"  {label}: {value} — obtained by: {evidence}"


def _fmt_system(facts: Mapping[str, Any]) -> list[str]:
    d = _section(facts, "system")
    if not d:
        return ["  no system facts (probe did not answer)"]
    shells = ", ".join(d.get("shells") or []) or "none found"
    elevated = {True: "yes (uid 0)", False: "no", None: "unknown"}.get(d.get("elevated"), "unknown")
    return [
        _with_evidence("os", str(d.get("os") or "unknown"), "platform.system()"),
        _with_evidence("kernel", str(d.get("kernel") or "unknown"), "platform.release()"),
        _with_evidence("arch", str(d.get("arch") or "unknown"), "platform.machine()"),
        _with_evidence("python", str(d.get("python") or "unknown"), "platform.python_version()"),
        _with_evidence("init", str(d.get("init") or "unknown"), "PID 1 (/proc/1/comm)"),
        _with_evidence(
            "container",
            str(d.get("container") or "none detected"),
            "/.dockerenv, /run/.containerenv, /proc/1/cgroup",
        ),
        _with_evidence("elevated", elevated, "os.geteuid() == 0"),
        _with_evidence("shells on PATH", shells, "shutil.which() over bash/zsh/fish/sh/pwsh"),
    ]


def _fmt_cpu(facts: Mapping[str, Any]) -> list[str]:
    d = _section(facts, "cpu")
    if not d:
        return ["  no CPU facts (probe did not answer)"]
    return [
        _with_evidence("logical cores", str(d.get("cores") or "unknown"), "os.cpu_count()"),
        _with_evidence("model", str(d.get("model") or "unknown"), _evidence(facts, "cpu")),
        _with_evidence("architecture", str(d.get("arch") or "unknown"), "platform.machine()"),
    ]


def _fmt_memory(facts: Mapping[str, Any]) -> list[str]:
    d = _section(facts, "memory")
    if not d:
        return ["  no memory facts (probe did not answer)"]
    estimated = " (available is an estimate)" if d.get("estimate") else ""
    return [
        _with_evidence("total RAM", _card.size(d.get("total_bytes")), _evidence(facts, "memory")),
        _with_evidence("available RAM", _card.size(d.get("available_bytes")) + estimated,
                       "MemAvailable / GlobalMemoryStatusEx / vm_stat"),
    ]


def _fmt_gpu(facts: Mapping[str, Any]) -> list[str]:
    d = _section(facts, "gpu")
    if not d:
        return ["  no GPU facts (probe did not answer)"]
    lines = [
        _with_evidence("vendor", str(d.get("vendor") or "unknown"), _evidence(facts, "gpu")),
        _with_evidence("compute capable", str(d.get("compute")), "driver tool + device listing"),
    ]
    for device in (d.get("devices") or [])[:4]:
        if isinstance(device, Mapping):
            vram = device.get("vram_mb")
            vram_txt = f" · {float(vram) / 1024:.0f} GB VRAM" if vram else ""
            lines.append(f"  device: {device.get('name')}{vram_txt}")
    if d.get("vram_mb"):
        lines.append(_with_evidence("VRAM", f"{float(d['vram_mb']) / 1024:.1f} GB", "nvidia-smi memory.total"))
    if d.get("note"):
        lines.append(f"  note: {d['note']}")
    return lines


def _fmt_disk(facts: Mapping[str, Any]) -> list[str]:
    d = _section(facts, "disk")
    if not d:
        return ["  no disk facts (probe did not answer)"]
    return [
        _with_evidence("free", _card.size(d.get("free_bytes")), "shutil.disk_usage()"),
        _with_evidence("total", _card.size(d.get("total_bytes")), "shutil.disk_usage()"),
        _with_evidence("volume", str(d.get("label") or "hermes data volume"),
                       "the filesystem holding the Hermes home (path deliberately not printed)"),
    ]


def _fmt_tools(facts: Mapping[str, Any]) -> list[str]:
    d = _section(facts, "tools")
    items = d.get("items") or {}
    if not items:
        return ["  no tool facts (probe did not answer)"]
    lines = []
    for name in sorted(items):
        item = items.get(name)
        if not isinstance(item, Mapping):
            continue
        if item.get("present"):
            version = item.get("version")
            lines.append(f"  {name}: {'version ' + str(version) if version else 'present (version not parsed)'}"
                         f" — obtained by: {item.get('source')}")
        else:
            lines.append(f"  {name}: absent — obtained by: {item.get('source')}")
    browsers = d.get("browsers") or {}
    present_browsers = [name for name, item in browsers.items()
                        if isinstance(item, Mapping) and item.get("present")]
    lines.append(f"  browser binaries: {', '.join(present_browsers) if present_browsers else 'none found'}"
                 f" — obtained by: shutil.which() over {', '.join(browsers) or 'chromium/chrome'}")
    docker = d.get("docker") or {}
    daemon = docker.get("daemon") or {}
    lines.append(f"  docker daemon: {daemon.get('status') or 'unknown'}"
                 + (f" ({daemon.get('reason')})" if daemon.get("reason") else "")
                 + f" — obtained by: {daemon.get('source') or 'not probed'}")
    return lines


_FORMATTERS = {
    "system": _fmt_system,
    "cpu": _fmt_cpu,
    "memory": _fmt_memory,
    "gpu": _fmt_gpu,
    "disk": _fmt_disk,
    "tools": _fmt_tools,
}

_TITLES = {
    "system": "System",
    "cpu": "CPU",
    "memory": "Memory",
    "gpu": "GPU",
    "disk": "Disk",
    "tools": "Tools",
}


def render_specs(facts: Mapping[str, Any], section: str = "all") -> str:
    """Human-readable fact sheet with the provenance of every section."""
    names = list(probes.SECTIONS) if section == "all" else [section]
    header = (
        f"machine specs · {section} · measured {_age(facts)}"
        f" · cache TTL {get_settings().ttl_hours}h · every value names how it was obtained"
    )
    blocks = [header]
    for name in names:
        status = _status(facts, name)
        blocks.append("")
        blocks.append(f"[{_TITLES.get(name, name)}] status: {status} · probed {_stamp(facts, name)}")
        blocks.extend(_FORMATTERS[name](facts))
    if section == "all":
        blocks.append("")
        blocks.append(
            "This sheet is measured, not assumed. Quote these values instead of guessing what "
            "the machine has; use machine_can(task) for a feasibility heuristic and "
            "machine_refresh(section) when the machine may have changed."
        )
    return "\n".join(blocks)


def machine_specs(args: dict | None = None, **kwargs: Any) -> str:
    """Return the measured fact sheet for one section, or all of them."""
    try:
        data = args if isinstance(args, dict) else {}
        raw = data.get("section")
        section = str(raw if raw is not None else "all").strip().lower()
        valid = ("all",) + probes.SECTIONS
        if section not in valid:
            return (
                f"Error: unknown section {section!r}. Valid sections: {', '.join(valid)}.\n"
                "Use machine_specs(section=\"all\") for everything."
            )
        settings = get_settings()
        facts = probes.measure(settings)
        return render_specs(facts, section)
    except Exception as exc:  # a tool must answer
        return f"Error: could not read machine specs ({type(exc).__name__}). Try machine_refresh(section=\"all\")."


# ───────────────────────────────────────────────────────────── feasibility


_QUANT_PATTERNS: tuple[tuple[str, int, str], ...] = (
    (r"\b(?:2[- ]?bit|q2(?:_k)?|int2)\b", 2, "2-bit"),
    (r"\b(?:4[- ]?bit|q4(?:_[a-z0-9_]+)?|int4|nf4)\b", 4, "4-bit"),
    (r"\b(?:5[- ]?bit|q5(?:_[a-z0-9_]+)?)\b", 5, "5-bit"),
    (r"\b(?:6[- ]?bit|q6(?:_k)?)\b", 6, "6-bit"),
    (r"\b(?:8[- ]?bit|q8(?:_0)?|int8)\b", 8, "8-bit"),
    (r"\b(?:fp16|f16|float16|half precision)\b", 16, "fp16"),
    (r"\b(?:fp32|f32|float32|full precision)\b", 32, "fp32"),
)

_PARAMS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:b\b|billion\b)", re.IGNORECASE)

_MODEL_HINTS = (
    "llm", "language model", " model", "llama", "mistral", "mixtral", "qwen", "gemma",
    "phi-", "deepseek", "gguf", "quant", "vram", "tokens/s", "token/s", "inference",
)
_DOCKER_HINTS = ("docker", "container", "dockerfile", "docker-compose", "compose up")
_CUDA_HINTS = ("cuda", "cudnn", "nvidia", "gpu training", "train a model")
_BROWSER_HINTS = ("browser", "playwright", "puppeteer", "selenium", "chromium", "headless chrome", "scrape a site")
_VIDEO_HINTS = ("ffmpeg", "video", "encode", "transcode", "h.264", "h264", "hevc", "mp4")
_SYSTEMD_HINTS = ("systemd", "systemctl", "system service", "daemon service", "unit file")


def _quantization(task: str) -> tuple[int, str, bool]:
    """Return ``(bits, label, assumed)`` for the precision the task implies."""
    lowered = task.lower()
    for pattern, bits, label in _QUANT_PATTERNS:
        if re.search(pattern, lowered):
            return bits, label, False
    if "quant" in lowered or "gguf" in lowered:
        return 4, "4-bit (assumed: quantization mentioned, precision not stated)", True
    return 16, "fp16 (assumed: no quantization stated)", True


def _params_billions(task: str) -> float | None:
    for match in _PARAMS_RE.finditer(task):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if 0.1 <= value <= 2000:
            return value
    return None


def _capacity(facts: Mapping[str, Any]) -> tuple[float | None, str, dict[str, Any]]:
    """Usable memory for a model, in bytes, plus where it comes from."""
    memory = _section(facts, "memory")
    gpu = _section(facts, "gpu")
    total = memory.get("total_bytes")
    available = memory.get("available_bytes")
    vram_mb = gpu.get("vram_mb")
    compute = gpu.get("compute")
    vendor = str(gpu.get("vendor") or "unknown")

    if gpu and compute and vram_mb:
        usable = float(vram_mb) * 1024 * 1024 * 0.9
        return usable, f"GPU VRAM ({vendor}, 90% of {float(vram_mb) / 1024:.1f} GB)", gpu
    if gpu and vendor == "apple":
        base = float(available or 0) or float(total or 0) * 0.7
        return base * 0.65, "Apple unified memory (65% usable by the GPU)", gpu
    if available:
        return float(available) * 0.9, "available system RAM (90% of MemAvailable), CPU inference", memory
    if total:
        return float(total) * 0.6, "system RAM (60% of total; available was not measurable), CPU inference", memory
    return None, "unknown (memory probe did not answer)", memory


def _model_verdict(task: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    params = _params_billions(task)
    bits, quant_label, assumed = _quantization(task)
    if params is None:
        return {
            "kind": "model",
            "verdict": "uncertain",
            "why": "the task mentions a model but not its size; the answer depends on the parameter count",
            "arithmetic": [
                "no parameter count found in the task (expected e.g. '7B', '13b', '70 billion')",
            ],
            "caveats": [
                "re-ask with a size: machine_can(\"run a 13B model at 4-bit\")",
                f"measured RAM: {_card.size(_section(facts, 'memory').get('available_bytes'))} available, "
                f"{_card.size(_section(facts, 'memory').get('total_bytes'))} total",
            ],
            "facts": ["memory", "gpu"],
        }

    bytes_per_param = (bits / 8.0) * (1.1 if bits <= 8 else 1.0)
    weights = params * 1e9 * bytes_per_param
    overhead = max(1.0e9, weights * 0.25) + 0.5e9
    need = weights + overhead
    capacity, capacity_label, _source = _capacity(facts)

    arithmetic = [
        f"{params:g}B params × {bytes_per_param:.3f} bytes/param ({quant_label}) = {_gb(weights)} of weights",
        f"runtime overhead (KV cache, context, allocator) ≈ 25% of weights + 0.5 GB = {_gb(overhead)}",
        f"total need ≈ {_gb(need)}",
    ]
    caveats: list[str] = []
    if assumed:
        caveats.append(f"precision was not stated, so {quant_label.split(' (')[0]} was assumed")

    if capacity is None:
        verdict = "uncertain"
        why = "the memory probe did not answer, so there is nothing to compare the model against"
        arithmetic.append("capacity: unknown")
    else:
        arithmetic.append(
            f"capacity: {capacity_label} = {_gb(capacity)} usable "
            f"({_card.size(capacity)}); need is {need / capacity * 100:.0f}% of it"
        )
        if need <= capacity * 0.95:
            verdict = "supported"
            why = f"the model fits: {_gb(need)} needed against {_gb(capacity)} usable."
        elif need <= capacity * 1.2:
            verdict = "uncertain"
            why = (
                f"the model is right at the edge: {_gb(need)} needed against {_gb(capacity)} usable — "
                "it may fit only with a smaller context or a tighter quantization."
            )
        else:
            verdict = "not supported"
            why = (
                f"the model does not fit: {_gb(need)} needed against {_gb(capacity)} usable "
                f"(short by {_gb(need - capacity)})."
            )

    gpu = _section(facts, "gpu")
    if verdict != "not supported" and not (gpu.get("compute") and gpu.get("vram_mb")):
        cores = _section(facts, "cpu").get("cores") or 2
        try:
            rate = max(0.2, min(400.0, float(cores) * 1.4 / max(params / 7.0, 0.1)))
        except (TypeError, ValueError):
            rate = 2.0
        caveats.append(
            f"no compute GPU with VRAM: this runs on {cores} CPU core(s) at roughly "
            f"{rate:.1f} tokens/s (rough estimate); a 500-token answer would take about "
            f"{500 / rate / 60:.0f} min"
        )
    if bits <= 4:
        caveats.append("quality drops at 4-bit; expect a measurable loss on reasoning tasks")

    return {
        "kind": "model",
        "verdict": verdict,
        "why": why,
        "arithmetic": arithmetic,
        "caveats": caveats,
        "facts": ["memory", "gpu", "cpu"],
    }


def _docker_verdict(task: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    tools = _section(facts, "tools")
    docker = tools.get("docker") or {}
    daemon = docker.get("daemon") or {}
    binary = bool(docker.get("binary"))
    status = str(daemon.get("status") or "unknown")
    arithmetic = [
        f"requirement 1: a docker binary on PATH → {'present' if binary else 'absent'}",
        f"requirement 2: a reachable daemon (`docker info` answers) → "
        + (status if binary else "not testable without the binary"),
    ]
    caveats: list[str] = []
    if binary and status == "reachable":
        server = daemon.get("server")
        verdict = "supported"
        why = f"the binary is installed and the daemon answers{f' (server {server})' if server else ''}."
        arithmetic.append("both requirements met")
    elif not binary:
        verdict = "not supported"
        why = "there is no docker binary on PATH, so no container can be run until it is installed."
    elif status == "unreachable":
        verdict = "not supported"
        why = "the docker binary exists but no daemon is answering, so `docker run` would fail."
        caveats.append("a daemon would have to be started (dockerd needs root or a rootless setup)")
    elif status == "unknown":
        verdict = "uncertain"
        why = "the binary is installed but the daemon probe could not establish whether a daemon answers."
        caveats.append(daemon.get("reason") or "probe timed out")
    else:
        verdict = "uncertain"
        why = "the docker state could not be established from the measured facts."
    return {
        "kind": "docker",
        "verdict": verdict,
        "why": why,
        "arithmetic": arithmetic,
        "caveats": caveats,
        "facts": ["tools"],
    }


def _cuda_verdict(task: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    gpu = _section(facts, "gpu")
    vendor = str(gpu.get("vendor") or "unknown")
    compute = gpu.get("compute")
    devices = [d.get("name") for d in (gpu.get("devices") or []) if isinstance(d, Mapping)]
    arithmetic = [
        f"requirement: a CUDA device with a working driver (nvidia-smi answers) → {gpu.get('note') or vendor}",
        f"vendor: {vendor}; compute capable: {compute}",
    ]
    if vendor == "nvidia" and compute:
        return {
            "kind": "cuda",
            "verdict": "supported",
            "why": f"nvidia-smi answered with {len(devices) or 'at least one'} device(s).",
            "arithmetic": arithmetic,
            "caveats": ["the CUDA toolkit (`nvcc`) and a matching driver version still have to be installed"],
            "facts": ["gpu", "tools"],
        }
    if vendor == "nvidia" and compute is False:
        verdict = "not supported"
        why = "an NVIDIA GPU is visible on the bus but nvidia-smi is absent, so the driver is not usable."
    elif vendor == "amd":
        verdict = "uncertain"
        why = "an AMD device is present; whether a working ROCm stack is installed was not established."
    else:
        verdict = "not supported"
        why = f"no CUDA device was detected (GPU vendor: {vendor})."
    return {"kind": "cuda", "verdict": verdict, "why": why, "arithmetic": arithmetic,
            "caveats": [], "facts": ["gpu"]}


def _browser_verdict(task: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    tools = _section(facts, "tools")
    browsers = tools.get("browsers") or {}
    present = [name for name, item in browsers.items() if isinstance(item, Mapping) and item.get("present")]
    items = tools.get("items") or {}
    node = bool((items.get("node") or {}).get("present"))
    npm = bool((items.get("npm") or {}).get("present"))
    arithmetic = [
        f"requirement: a chromium/chrome binary on PATH → {', '.join(present) if present else 'none found'}",
        f"alternatives: node {'present' if node else 'absent'}, npm {'present' if npm else 'absent'} "
        "(needed to install Playwright/Puppeteer's browser)",
    ]
    if present:
        return {"kind": "browser", "verdict": "supported",
                "why": f"a browser binary is installed ({', '.join(present)}).",
                "arithmetic": arithmetic, "caveats": [], "facts": ["tools"]}
    if node and npm:
        return {
            "kind": "browser", "verdict": "uncertain",
            "why": "no browser binary is installed, but node and npm are, so a headless browser can be installed.",
            "arithmetic": arithmetic,
            "caveats": ["installing Playwright's Chromium downloads ~150 MB and needs network access"],
            "facts": ["tools"],
        }
    return {"kind": "browser", "verdict": "not supported",
            "why": "no chromium/chrome binary, and no node+npm pair to install one with.",
            "arithmetic": arithmetic, "caveats": [], "facts": ["tools"]}


def _video_verdict(task: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    tools = _section(facts, "tools")
    items = tools.get("items") or {}
    ffmpeg = items.get("ffmpeg") or {}
    disk = _section(facts, "disk")
    free = disk.get("free_bytes")
    lowered = task.lower()
    if re.search(r"\b4k\b|2160p|\buhd\b", lowered):
        resolution, mbps = "4K (2160p)", 40.0
    elif re.search(r"1440p|\b2k\b", lowered):
        resolution, mbps = "1440p", 16.0
    elif re.search(r"1080p|\bfhd\b", lowered):
        resolution, mbps = "1080p", 8.0
    else:
        resolution, mbps = "1080p (assumed)", 8.0

    duration = 60.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(s\b|sec|second|m\b|min|minute|h\b|hour)", lowered)
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        duration = value * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)

    needed = mbps * 1e6 / 8 * duration * 1.2 + 0.5e9
    arithmetic = [
        f"requirement 1: an ffmpeg binary → {'present' if ffmpeg.get('present') else 'absent'}"
        + (f" ({ffmpeg.get('version')})" if ffmpeg.get("version") else ""),
        f"estimate: {resolution} at ~{mbps:.0f} Mbit/s for {duration:.0f}s ≈ {_gb(needed)} "
        "(1.2× muxing headroom + 0.5 GB working space)",
        f"free disk: {_card.size(free)}",
    ]
    caveats: list[str] = []
    if not ffmpeg.get("present"):
        return {"kind": "video", "verdict": "not supported",
                "why": "ffmpeg is not installed, so no encode can run.",
                "arithmetic": arithmetic, "caveats": [], "facts": ["tools", "disk"]}
    if free is None:
        return {"kind": "video", "verdict": "uncertain",
                "why": "ffmpeg is installed but free disk space could not be measured.",
                "arithmetic": arithmetic, "caveats": [], "facts": ["tools", "disk"]}
    if float(free) < needed:
        verdict = "not supported"
        why = f"ffmpeg is installed but the output would not fit: {_gb(needed)} needed, {_card.size(free)} free."
    elif float(free) < needed * 2:
        verdict = "uncertain"
        why = (
            f"ffmpeg is installed and the estimate fits, but only just: {_gb(needed)} needed, "
            f"{_card.size(free)} free."
        )
    else:
        verdict = "supported"
        why = f"ffmpeg is installed and {_card.size(free)} free easily covers ~{_gb(needed)}."
    cores = _section(facts, "cpu").get("cores") or 2
    gpu = _section(facts, "gpu")
    if not (gpu.get("compute") and gpu.get("vendor") == "nvidia"):
        per_second = 1.0 if resolution.startswith("4K") else 4.0
        caveats.append(
            f"software encode on {cores} CPU core(s): roughly {per_second:.0f} frame(s)/s at {resolution.split(' ')[0]}, "
            f"so a {duration:.0f}s clip at 30fps takes about {30 * duration / per_second / 60:.0f} min "
            "(rough estimate; hardware encoders were not probed)"
        )
    caveats.append("the bitrate is a planning estimate, not a measurement of ffmpeg's output")
    return {"kind": "video", "verdict": verdict, "why": why, "arithmetic": arithmetic,
            "caveats": caveats, "facts": ["tools", "disk", "cpu", "gpu"]}


def _systemd_verdict(task: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    system = _section(facts, "system")
    tools = _section(facts, "tools")
    items = tools.get("items") or {}
    systemctl = bool((items.get("systemctl") or {}).get("present"))
    init = str(system.get("init") or "unknown")
    container = system.get("container")
    arithmetic = [
        f"requirement 1: systemd as PID 1 → {init}",
        f"requirement 2: a systemctl binary → {'present' if systemctl else 'absent'}",
    ]
    caveats: list[str] = []
    if init == "systemd" and systemctl:
        verdict = "supported"
        why = "systemd is PID 1 and systemctl is installed."
        if container:
            caveats.append(f"this process is inside {container}: /run/systemd may be isolated from the host")
        caveats.append("installing a unit needs root; the measured privileges are shown in machine_specs(section=\"system\")")
    elif init == "systemd":
        verdict = "not supported"
        why = "systemd is running but systemctl is not installed."
    elif systemctl:
        verdict = "not supported"
        why = f"systemctl exists but PID 1 is {init}, so units cannot be started here."
    else:
        verdict = "not supported"
        why = f"no systemd: PID 1 is {init} and systemctl is absent."
    return {"kind": "systemd", "verdict": verdict, "why": why, "arithmetic": arithmetic,
            "caveats": caveats, "facts": ["system", "tools"]}


def _classify(task: str) -> str:
    lowered = task.lower()
    # A parameter count is the strongest signal: "run a 7B model with CUDA" is a
    # question about whether the model fits, not about the CUDA toolkit.
    if _params_billions(task) is not None:
        return "model"
    for kind, hints in (
        ("docker", _DOCKER_HINTS),
        ("cuda", _CUDA_HINTS),
        ("browser", _BROWSER_HINTS),
        ("video", _VIDEO_HINTS),
        ("systemd", _SYSTEMD_HINTS),
    ):
        if any(hint in lowered for hint in hints):
            return kind
    if any(hint in lowered for hint in _MODEL_HINTS):
        return "model"
    return "unknown"


_ASSESSORS = {
    "model": _model_verdict,
    "docker": _docker_verdict,
    "cuda": _cuda_verdict,
    "browser": _browser_verdict,
    "video": _video_verdict,
    "systemd": _systemd_verdict,
}


def _fact_digest(facts: Mapping[str, Any], names: Iterable[str]) -> list[str]:
    lines = []
    for name in names:
        data = _section(facts, name)
        if name == "memory":
            lines.append(
                f"ram: {_card.size(data.get('available_bytes'))} available of "
                f"{_card.size(data.get('total_bytes'))} — {_evidence(facts, 'memory')}"
            )
        elif name == "gpu":
            devices = [str(d.get("name")) for d in (data.get("devices") or []) if isinstance(d, Mapping)]
            lines.append(
                f"gpu: {data.get('vendor') or 'unknown'}"
                + (f" ({', '.join(devices[:2])})" if devices else "")
                + f", compute={data.get('compute')} — {_evidence(facts, 'gpu')}"
            )
        elif name == "cpu":
            lines.append(f"cpu: {data.get('cores')} logical core(s) {data.get('model') or ''}".rstrip()
                         + f" — {_evidence(facts, 'cpu')}")
        elif name == "disk":
            lines.append(f"disk: {_card.size(data.get('free_bytes'))} free of "
                         f"{_card.size(data.get('total_bytes'))} — {_evidence(facts, 'disk')}")
        elif name == "system":
            lines.append(f"init: {data.get('init')} · container: {data.get('container') or 'none'}"
                         f" — {_evidence(facts, 'system')}")
        elif name == "tools":
            items = data.get("items") or {}
            present = [f"{n} {v.get('version') or ''}".strip() for n, v in items.items()
                       if isinstance(v, Mapping) and v.get("present")]
            shown = ", ".join(present[:8]) or "none of the measured ones"
            if len(present) > 8:
                shown += f", +{len(present) - 8} more"
            lines.append(f"tools present: {shown} — shutil.which() + `<tool> --version`")
    return lines


def _unknown_verdict(task: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "unknown",
        "verdict": "uncertain",
        "why": (
            "no feasibility rule matched this task, so specs will not guess. It can reason about "
            "local LLMs, Docker/containers, CUDA, headless browsers, ffmpeg video work and systemd services."
        ),
        "arithmetic": [f"task as given: {task[:200]}"],
        "caveats": [
            "read machine_specs and decide from the measured facts, or rephrase the task around one of the "
            "supported categories",
        ],
        "facts": ["system", "cpu", "memory", "gpu", "disk", "tools"],
    }


def assess(task: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a task and return a verdict dict. Never raises for normal input."""
    kind = _classify(task)
    assessor = _ASSESSORS.get(kind)
    result = assessor(task, facts) if assessor else _unknown_verdict(task, facts)
    result.setdefault("facts", [])
    result["kind"] = kind
    return result


def render_verdict(task: str, result: Mapping[str, Any], facts: Mapping[str, Any]) -> str:
    verdict = str(result.get("verdict") or "uncertain")
    lines = [
        f'machine_can("{task[:200]}") — {HEURISTIC_NOTE}',
        f"verdict: {verdict}",
        f"why: {result.get('why') or 'no reasoning recorded'}",
        f"matched: {result.get('kind') or 'unknown'}",
        "",
        "arithmetic:",
    ]
    lines.extend(f"  - {item}" for item in (result.get("arithmetic") or ["(none)"]))
    caveats = list(result.get("caveats") or [])
    if caveats:
        lines.append("")
        lines.append("caveats and assumptions:")
        lines.extend(f"  - {item}" for item in caveats)
    digest = _fact_digest(facts, result.get("facts") or [])
    if digest:
        lines.append("")
        lines.append("measured facts used:")
        lines.extend(f"  - {item}" for item in digest)
    lines.append("")
    lines.append(
        f"measured {time.strftime('%Y-%m-%d', time.gmtime(probes.measured_epoch(facts)))}; "
        "re-check with machine_specs(section=\"...\") or machine_refresh(). "
        "Verdicts describe this machine as measured, not what a different setup could do."
    )
    return "\n".join(lines)


def machine_can(args: dict | None = None, **kwargs: Any) -> str:
    """Heuristic feasibility verdict for a task, with the arithmetic behind it."""
    try:
        data = args if isinstance(args, dict) else {}
        task = " ".join(str(data.get("task") or "").split())
        if not task:
            return (
                "Error: machine_can needs a task, e.g. "
                'machine_can(task="run a 7B model at 4-bit") or '
                'machine_can(task="run Docker containers").'
            )
        settings = get_settings()
        facts = probes.measure(settings)
        result = assess(task, facts)
        return render_verdict(task, result, facts)
    except Exception as exc:  # a tool must answer
        return (
            f"Error: machine_can could not evaluate the task ({type(exc).__name__}). "
            "This is a heuristic tool; read machine_specs and decide from the measured facts instead."
        )


def machine_refresh(args: dict | None = None, **kwargs: Any) -> str:
    """Re-probe one section (or all) and update the cache."""
    try:
        data = args if isinstance(args, dict) else {}
        raw = data.get("section")
        section = str(raw if raw is not None else "all").strip().lower()
        valid = ("all",) + probes.SECTIONS
        if section not in valid:
            return (
                f"Error: unknown section {section!r}. Valid sections: {', '.join(valid)}."
            )
        settings = get_settings()
        started = time.time()
        force = probes.SECTIONS if section == "all" else (section,)
        facts = probes.measure(settings, force=force)
        try:
            from . import invalidate_card

            invalidate_card()
        except Exception:  # pragma: no cover - the memo is an optimisation
            pass
        elapsed = time.time() - started
        names = list(probes.SECTIONS) if section == "all" else [section]
        stored = probes.load_cache()
        try:
            persisted = bool(stored) and all(
                stored["sections"].get(name, {}).get("measured_epoch")
                == facts["sections"].get(name, {}).get("measured_epoch")
                for name in names
            )
        except Exception:  # pragma: no cover - a malformed cache is just "not persisted"
            persisted = False
        header = (
            f"refreshed {section} in {elapsed:.2f}s · re-probed {', '.join(names)} · "
            + ("cache updated on disk" if persisted
               else "cache NOT written (check that plugin-data is writable)")
        )
        return header + "\n\n" + render_specs(facts, section)
    except Exception as exc:  # a tool must answer
        return f"Error: machine_refresh failed ({type(exc).__name__}); the previous cache is untouched."


__all__ = [
    "machine_specs",
    "machine_can",
    "machine_refresh",
    "render_specs",
    "assess",
    "render_verdict",
]
