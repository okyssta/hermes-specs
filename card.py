"""The capability card: a bounded, privacy-scrubbed sentence-per-fact summary.

This module is pure rendering — it takes the facts dict that :mod:`probes`
produced and returns a string that fits in the budget core gives a system-prompt
section. Two properties matter more than prettiness:

* **It must never exceed the cap.** Core *drops* an oversized section rather than
  truncating it, so an over-long card would silently disappear.
* **It must never carry an identifier.** The card is frozen into every new
  session's system prompt, so it is built only from whitelisted fields and then
  scrubbed again: paths, IP addresses, MAC addresses and e-mail addresses are
  replaced with ``(path)`` / ``(redacted)``.

Nothing here runs a probe or touches the disk.
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterable, Mapping

from .settings import CARD_MAX_CHARS

#: Order in which the card lists installed tools (matches settings.DEFAULT_TOOLS).
TOOL_PRIORITY = (
    "python3", "pip", "uv", "node", "npm", "git", "ffmpeg", "curl", "jq",
    "sqlite3", "tesseract", "docker", "podman", "systemctl", "apt", "dnf",
    "pacman", "brew", "winget",
)

#: Absences worth one short clause, because a plan commonly depends on them.
NOTABLE_ABSENT = ("docker", "ffmpeg", "git", "node", "python3", "systemctl")

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
_UNIX_PATH_RE = re.compile(r"(?<![\w.])(?:/(?:[\w.@+-]+)){2,}/?")
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/](?:[^\s,;]+[\\/])?[^\s,;]*")


def scrub(text: str) -> str:
    """Remove anything that looks like an identifier, path or address."""
    value = str(text or "")
    value = _EMAIL_RE.sub("(redacted)", value)
    value = _MAC_RE.sub("(redacted)", value)
    value = _IPV4_RE.sub("(redacted)", value)
    value = _WIN_PATH_RE.sub("(path)", value)
    value = _UNIX_PATH_RE.sub("(path)", value)
    return value


def size(value: float | int | None) -> str:
    """Binary-prefix size, e.g. ``11.6 GiB``; ``unknown`` when not measured."""
    if value is None:
        return "unknown"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if number <= 0:
        return "0 GiB"
    gib = number / (1024 ** 3)
    if gib < 1:
        return f"{number / (1024 ** 2):.0f} MiB"
    return f"{gib:.1f} GiB"


def _section(facts: Mapping[str, Any], name: str) -> dict[str, Any]:
    entry = (facts.get("sections") or {}).get(name)
    if not isinstance(entry, Mapping):
        return {}
    data = entry.get("data")
    return dict(data) if isinstance(data, Mapping) else {}


def _status(facts: Mapping[str, Any], name: str) -> str:
    entry = (facts.get("sections") or {}).get(name)
    if not isinstance(entry, Mapping):
        return "unknown"
    return str(entry.get("status") or "unknown")


def _measured_date(facts: Mapping[str, Any]) -> str:
    stamps = []
    for entry in (facts.get("sections") or {}).values():
        if isinstance(entry, Mapping):
            try:
                stamps.append(float(entry.get("measured_epoch")))
            except (TypeError, ValueError):
                continue
    epoch = min(stamps) if stamps else time.time()
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def _os_line(facts: Mapping[str, Any]) -> str | None:
    system = _section(facts, "system")
    cpu = _section(facts, "cpu")
    os_name = system.get("os")
    kernel = system.get("kernel")
    arch = system.get("arch") or cpu.get("arch")
    if not os_name:
        return "os: unknown (system probe did not answer)"
    bits = [str(os_name)]
    if kernel and kernel != "unknown":
        bits.append(str(kernel))
    if arch and arch != "unknown":
        bits.append(str(arch))
    return "os: " + " · ".join(bits)


def _cpu_line(facts: Mapping[str, Any]) -> str | None:
    cpu = _section(facts, "cpu")
    cores = cpu.get("cores")
    model = cpu.get("model")
    if not cores and not model:
        return "cpu: unknown"
    left = f"{int(cores)} logical cores" if cores else "core count unknown"
    if model and model != "unknown":
        return f"cpu: {left} · {model}"
    return f"cpu: {left}"


def _ram_line(facts: Mapping[str, Any]) -> str | None:
    memory = _section(facts, "memory")
    total = memory.get("total_bytes")
    available = memory.get("available_bytes")
    if not total and not available:
        return "ram: unknown"
    if total and available:
        estimated = " (approx.)" if memory.get("estimate") else ""
        return f"ram: {size(available)}{estimated} available of {size(total)}"
    if total:
        return f"ram: {size(total)} total (available unknown)"
    return f"ram: {size(available)} available (total unknown)"


def _gpu_line(facts: Mapping[str, Any]) -> str | None:
    gpu = _section(facts, "gpu")
    vendor = str(gpu.get("vendor") or "unknown")
    devices = gpu.get("devices") or []
    names = [str(d.get("name")) for d in devices if isinstance(d, Mapping) and d.get("name")]
    vram = gpu.get("vram_mb")
    compute = gpu.get("compute")
    if vendor == "nvidia" and compute:
        vram_txt = f" · {float(vram) / 1024:.0f} GB VRAM" if vram else ""
        return f"gpu: {names[0] if names else 'NVIDIA GPU'}{vram_txt} · CUDA"
    if vendor == "amd" and compute:
        return f"gpu: {names[0] if names else 'AMD GPU'} · ROCm"
    if vendor == "apple":
        return "gpu: Apple integrated GPU · Metal (unified memory)"
    if vendor == "none":
        return "gpu: none detected"
    if vendor == "virtual":
        return "gpu: virtual display only (no CUDA/ROCm)"
    if vendor == "other" and names:
        return f"gpu: {names[0]} (compute stack not verified)"
    if vendor == "nvidia":
        return "gpu: NVIDIA present, driver unusable (no nvidia-smi)"
    return "gpu: unknown"


def _disk_line(facts: Mapping[str, Any]) -> str | None:
    disk = _section(facts, "disk")
    free = disk.get("free_bytes")
    total = disk.get("total_bytes")
    if free is None:
        return "disk: unknown"
    if total:
        return f"disk: {size(free)} free of {size(total)}"
    return f"disk: {size(free)} free"


def _entries(facts: Mapping[str, Any]) -> list[str]:
    tools = _section(facts, "tools")
    items = tools.get("items") or {}
    if not isinstance(items, Mapping):
        return []
    found: list[str] = []
    for name in TOOL_PRIORITY:
        item = items.get(name)
        if not isinstance(item, Mapping) or not item.get("present"):
            continue
        version = item.get("version")
        found.append(f"{name} {version}".strip() if version else str(name))
    return found


def _tools_line(entries: Iterable[str], omitted: int, absent: Iterable[str]) -> str:
    shown = list(entries)
    pieces = list(shown)
    if omitted > 0:
        pieces.append(f"+{omitted} more")
    absent_list = list(absent)
    if absent_list:
        pieces.append("no " + ", ".join(absent_list))
    if not pieces:
        return "tools: none of the measured tools are installed"
    return "tools: " + " · ".join(pieces)


def _absent(facts: Mapping[str, Any], present: Iterable[str]) -> list[str]:
    seen = set(present)
    return [name for name in NOTABLE_ABSENT if name not in seen][:2]


def _system_line(facts: Mapping[str, Any]) -> str | None:
    system = _section(facts, "system")
    init = system.get("init")
    container = system.get("container")
    elevated = system.get("elevated")
    if not init and container is None and elevated is None:
        return None
    bits = []
    if init and init != "unknown":
        bits.append(f"{init} (pid 1)" if str(system.get("os")) == "Linux" else str(init))
    if container:
        bits.append(f"inside {container}")
    elif container is None and init:
        bits.append("not containerized")
    if elevated is True:
        bits.append("elevated")
    elif elevated is False:
        bits.append("unprivileged user")
    return "system: " + " · ".join(bits) if bits else None


def _join(header: str, body: list[str], footer: str) -> str:
    return "\n".join([header] + [line for line in body if line] + [footer])


def render_card(facts: Mapping[str, Any] | None, max_chars: int = CARD_MAX_CHARS) -> str:
    """Render the card, guaranteed to be at most ``max_chars`` characters.

    Degrades in a fixed order when the budget is tight: drop tool entries from
    the end (``+N more``), drop optional body lines, then hard-truncate the body
    while keeping the header and the footer intact.
    """
    try:
        cap = max(40, int(max_chars))
    except (TypeError, ValueError):
        cap = CARD_MAX_CHARS

    header = "specs · machine capability card"
    try:
        facts = facts if isinstance(facts, Mapping) else {}
        footer = f"measured {_measured_date(facts)}; call machine_specs for detail"
        body = [
            _os_line(facts),
            _cpu_line(facts),
            _ram_line(facts),
            _gpu_line(facts),
            _disk_line(facts),
        ]
        body = [line for line in body if line]
        tail = _system_line(facts)
        entries = _entries(facts)
        omitted_absent = _absent(facts, [entry.split(" ", 1)[0] for entry in entries])

        for keep in range(len(entries), -1, -1):
            tools_line = _tools_line(entries[:keep], len(entries) - keep, omitted_absent) if entries else None
            candidate = body + ([tools_line] if tools_line else []) + ([tail] if tail else [])
            text = _join(header, candidate, footer)
            if len(text) <= cap:
                return scrub(text)

        # Even the shortest tool line does not fit: drop body lines one at a time
        # (gpu and disk last, they are the ones a plan usually needs).
        reduced = list(body)
        for index in (3, 4, 2, 1, 0):
            if index < len(reduced):
                reduced.pop(index)
                text = _join(header, reduced + ([tail] if tail else []), footer)
                if len(text) <= cap:
                    return scrub(text)

        text = _join(header, reduced + ([tail] if tail else []), footer)
        if len(text) <= cap:
            return scrub(text)

        room = cap - len(header) - len(footer) - 2
        if room < 1:
            return (header + " " + footer)[:cap]
        middle = "\n".join(reduced + ([tail] if tail else []))
        return scrub(header + "\n" + middle[:room].rstrip() + "\n" + footer)
    except Exception:
        # A section that cannot render must not remove the agent's knowledge that
        # the tool exists.
        fallback = (
            "specs · machine capability card\n"
            "card unavailable (rendering failed); call machine_specs for measured facts"
        )
        return fallback[:cap]


__all__ = ["render_card", "scrub", "size", "TOOL_PRIORITY", "NOTABLE_ABSENT"]
