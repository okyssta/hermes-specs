"""/specs — card, refresh, detail, can.

A command is the surface a person reaches for, so it answers in sentences, not
JSON. It returns text instead of printing (it may run inside the gateway), and
the only state it changes is this plugin's own cache.
"""

from __future__ import annotations

from typing import Any

from . import card as _card
from . import probes, tools
from .settings import Settings

_HELP = (
    "/specs                    the capability card (what every new session sees)\n"
    "/specs card               same, plus where the cache lives in the profile\n"
    "/specs refresh [section]  re-measure now (all, system, cpu, memory, gpu, disk, tools)\n"
    "/specs status             cache age, TTL, settings\n"
    "/specs can <task>         feasibility heuristic, e.g. /specs can run a 7B model at 4-bit\n"
    "/specs <section>          full fact sheet for one section (system, cpu, memory, gpu, disk, tools)\n"
    "/specs help               this text"
)


def _settings() -> Settings:
    from . import current_settings

    return current_settings()


def _cache_status(settings: Settings) -> str:
    facts = probes.load_cache()
    if not facts:
        return "cache: empty (the next call measures the machine and writes it)"
    age = probes.section_age(facts, "system")
    ttl = settings.ttl_hours
    state = "fresh" if age is not None and age <= settings.ttl_seconds else "stale"
    return (
        f"cache: {state} · oldest section measured {age / 60:.0f} min ago · TTL {ttl}h · "
        f"{len(facts.get('sections') or {})} section(s)"
    )


def handle(raw_args: str = "", **kwargs: Any) -> str:
    text = str(raw_args or "").strip()
    lowered = text.lower()
    settings = _settings()

    if lowered in {"", "card"}:
        facts = probes.measure(settings)
        body = _card.render_card(facts, settings.card_max_chars)
        if lowered == "card":
            return body + f"\n\n{_cache_status(settings)}\nstored at: <hermes home>/plugin-data/specs/specs.json"
        return body

    if lowered in {"status", "info"}:
        return "\n".join([_cache_status(settings), "", settings.describe()])

    if lowered in {"help", "?", "-h", "--help"}:
        return _HELP

    if lowered.startswith("refresh") or lowered.startswith("rescan"):
        section = text.split(maxsplit=1)[1].strip().lower() if len(text.split(maxsplit=1)) > 1 else "all"
        valid = ("all",) + probes.SECTIONS
        if section not in valid:
            return f"Unknown section {section!r}. Valid: {', '.join(valid)}."
        from . import invalidate_card

        invalidate_card()
        result = tools.machine_refresh({"section": section})
        return result

    if lowered.startswith("can "):
        task = text[4:].strip()
        return tools.machine_can({"task": task})

    if lowered in probes.SECTIONS:
        return tools.machine_specs({"section": lowered})

    if lowered.startswith("can"):
        return "Usage: /specs can <task>   e.g. /specs can run a 7B model at 4-bit"

    fact_sheet = tools.machine_specs({"section": "all"})
    return f"Unknown subcommand {text!r}.\n\n{_HELP}\n\n{fact_sheet}"


__all__ = ["handle"]
