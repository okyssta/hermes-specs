"""Settings resolution for the ``specs`` plugin.

Every key here mirrors a ``config_schema`` entry in ``plugin.yaml`` one-for-one:
the Desktop settings form, ``hermes config set`` and this module must agree on a
single source of truth, and ``tests/test_manifest.py`` asserts the key sets are
identical. Values are coerced defensively: a bad value in ``config.yaml`` must
never stop the plugin from answering, so every coercion falls back to the default
rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PLUGIN_ID = "specs"

SCHEMA_KEYS = (
    "enabled",
    "ttl_hours",
    "card_max_chars",
    "probe_timeout_ms",
    "gpu_probe",
    "docker_probe",
    "tools",
)

#: Tools measured for presence and version, in the order the card shows them.
#: The order is a priority: when the card has to shrink it drops from the end.
DEFAULT_TOOLS = (
    "python3",
    "pip",
    "uv",
    "node",
    "npm",
    "git",
    "ffmpeg",
    "curl",
    "jq",
    "sqlite3",
    "tesseract",
    "docker",
    "podman",
    "systemctl",
    "apt",
    "dnf",
    "pacman",
    "brew",
    "winget",
)

#: Browsers are measured separately: a task that needs one needs a *browser*,
#: not any of the tools above.
DEFAULT_BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome")

CARD_MAX_CHARS = 1200


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _names(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [part for part in value.replace(",", "\n").splitlines()]
    if not isinstance(value, (list, tuple)):
        return default
    seen: list[str] = []
    for entry in value:
        name = str(entry).strip()
        if not name or "/" in name or len(name) > 40 or name in seen:
            continue
        seen.append(name)
    return tuple(seen) or default


@dataclass(frozen=True)
class Settings:
    """Resolved plugin settings; immutable so a handler cannot change it mid-turn."""

    enabled: bool = True
    ttl_hours: int = 24
    card_max_chars: int = CARD_MAX_CHARS
    probe_timeout_ms: int = 1500
    gpu_probe: bool = True
    docker_probe: bool = True
    tools: tuple[str, ...] = DEFAULT_TOOLS

    @property
    def ttl_seconds(self) -> float:
        return float(self.ttl_hours) * 3600.0

    @property
    def timeout_seconds(self) -> float:
        return max(0.05, self.probe_timeout_ms / 1000.0)

    @classmethod
    def from_ctx(cls, ctx: Any) -> "Settings":
        get = getattr(ctx, "get_config", None)
        if not callable(get):  # pragma: no cover - manifest-only hosts
            return cls()

        def value(key: str, fallback: Any) -> Any:
            try:
                resolved = get(key, default=fallback)
            except TypeError:  # hosts whose get_config takes one argument
                try:
                    resolved = get(key)
                except Exception:
                    return fallback
            except Exception:
                return fallback
            return fallback if resolved is None else resolved

        return cls(
            enabled=_bool(value("enabled", True), True),
            ttl_hours=_int(value("ttl_hours", 24), 24, 1, 24 * 30),
            card_max_chars=_int(value("card_max_chars", CARD_MAX_CHARS), CARD_MAX_CHARS, 200, 4000),
            probe_timeout_ms=_int(value("probe_timeout_ms", 1500), 1500, 100, 10000),
            gpu_probe=_bool(value("gpu_probe", True), True),
            docker_probe=_bool(value("docker_probe", True), True),
            tools=_names(value("tools", DEFAULT_TOOLS), DEFAULT_TOOLS),
        )

    def describe(self) -> str:
        return "\n".join(
            [
                f"enabled: {self.enabled}",
                f"cache TTL: {self.ttl_hours}h",
                f"card cap: {self.card_max_chars} chars",
                f"probe timeout: {self.probe_timeout_ms}ms",
                f"GPU probe: {'on' if self.gpu_probe else 'off'}",
                f"docker daemon probe: {'on' if self.docker_probe else 'off'}",
                f"tools measured: {', '.join(self.tools)}",
            ]
        )
