"""specs — Hermes always knows what this machine can actually do.

Plans fail on hardware: an agent that assumes a GPU, a Docker daemon, ``brew`` or
32 GB of RAM writes instructions the user cannot follow, and neither side finds
out until the command fails. ``specs`` measures the machine once, caches a compact
capability card, freezes that card into every new session's system prompt, and
exposes three tools for the details and for feasibility checks.

What it adds:

* **``machine_specs(section)``** — a measured fact sheet (OS, CPU, RAM, GPU, disk,
  tools, init system) where every value names how it was obtained.
* **``machine_can(task)``** — a *labelled heuristic* verdict
  (supported / not supported / uncertain) with the arithmetic it used.
* **``machine_refresh(section)``** — re-probe after the machine changes.
* **``specs.card``** — a bounded system-prompt section (1200 chars by default)
  that tells every new session the real hardware and toolchain before it plans.
* **``/specs``** — card, detail, refresh and ``can <task>`` from the prompt.
* **A bundled skill** — teaches when to quote a measurement instead of guessing.

Privacy is a design constraint, not a setting: the card carries capability facts
only — no username, hostname, home path, serial number, MAC address, IP address or
environment-variable value. Everything stays on the machine; the plugin makes no
network calls and never starts a daemon.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from . import card, commands, probes, schemas, tools
from .settings import PLUGIN_ID, SCHEMA_KEYS, Settings

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

_CTX: Any = None
_CACHED: Settings | None = None
_CACHED_AT = 0.0
_CACHE_SECONDS = 2.0

#: The card is re-rendered at most once a minute inside one process: it walks the
#: cache file, and a session that renders several prompts should pay for that once.
_CARD_MEMO: dict[str, Any] = {"text": "", "at": 0.0}
_CARD_MEMO_SECONDS = 60.0


def current_settings() -> Settings:
    """Resolved settings, memoised for a couple of seconds (a turn calls them often)."""
    global _CACHED, _CACHED_AT

    now = time.monotonic()
    if _CACHED is not None and now - _CACHED_AT < _CACHE_SECONDS:
        return _CACHED
    try:
        _CACHED = Settings.from_ctx(_CTX) if _CTX is not None else Settings()
    except Exception:  # pragma: no cover - settings must never break a turn
        logger.debug("specs: could not read settings, using defaults", exc_info=True)
        _CACHED = Settings()
    _CACHED_AT = now
    return _CACHED


def invalidate_card() -> None:
    """Drop the memoised card so the next render re-reads (used after a refresh)."""
    _CARD_MEMO["text"] = ""
    _CARD_MEMO["at"] = 0.0


def card_section(info: Mapping[str, Any] | None = None) -> str:
    """Render the system-prompt section. Receives a read-only mapping; returns a str.

    Core drops a section that exceeds its ``max_chars``, so the renderer is given
    the same budget the section was registered with. Any failure returns ``""``:
    a broken card must not break a session's prompt.
    """
    try:
        settings = current_settings()
        if not settings.enabled:
            return ""
        now = time.monotonic()
        if _CARD_MEMO["text"] and now - float(_CARD_MEMO["at"]) < _CARD_MEMO_SECONDS:
            return str(_CARD_MEMO["text"])
        facts = probes.measure(settings)
        text = card.render_card(facts, settings.card_max_chars)
        _CARD_MEMO["text"] = text
        _CARD_MEMO["at"] = now
        return text
    except Exception:  # pragma: no cover - defensive
        logger.debug("specs: could not render the capability card", exc_info=True)
        return ""


def register(ctx: Any) -> None:
    """Register tools, the command, the prompt section and the bundled skill."""
    global _CTX

    _CTX = ctx
    settings = current_settings()

    handlers = {
        "machine_specs": tools.machine_specs,
        "machine_can": tools.machine_can,
        "machine_refresh": tools.machine_refresh,
    }
    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset=PLUGIN_ID,
            schema=schema,
            handler=handlers[schema["name"]],
        )

    ctx.register_command(
        PLUGIN_ID,
        commands.handle,
        description="What this machine can actually do: measured specs and feasibility checks",
    )

    if settings.enabled:
        try:
            ctx.register_system_prompt_section(
                "specs.card",
                card_section,
                position="after_memory",
                max_chars=settings.card_max_chars,
            )
        except Exception:  # pragma: no cover - optional surface, never fatal at load
            logger.warning("specs: could not register the system prompt section", exc_info=True)

    skill_dir = _skill_dir()
    if skill_dir is not None:
        try:
            ctx.register_skill(PLUGIN_ID, str(skill_dir))
        except Exception:  # pragma: no cover - optional surface
            logger.debug("specs: could not register the bundled skill", exc_info=True)

    logger.info(
        "specs ready: card cap %d chars, cache TTL %dh, %d tools measured",
        settings.card_max_chars,
        settings.ttl_hours,
        len(settings.tools),
    )


def _skill_dir():
    try:
        from pathlib import Path

        candidate = Path(__file__).parent / "skills" / "specs"
        return candidate if (candidate / "SKILL.md").is_file() else None
    except Exception:  # pragma: no cover
        return None


__all__ = ["register", "current_settings", "card_section", "invalidate_card",
           "PLUGIN_ID", "SCHEMA_KEYS", "probes", "card", "tools"]
