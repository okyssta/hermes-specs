#!/usr/bin/env python3
"""End-to-end demo: measure this host, print the card, then judge three tasks.

Runs in a throwaway ``HERMES_HOME`` (so it never touches a real profile), makes no
network calls, and prints the evidence it used — the card that would be frozen
into a session prompt, the three feasibility verdicts, and proof that the second
measurement comes from the cache.

    python scripts/demo.py                 # fresh temp home
    python scripts/demo.py .demo-home      # reuse a home: second run hits the cache

Exit status is 0 only when the card fits its budget, the cache file exists, and a
warm measurement is faster than a cold one.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

DEMO_TASKS = (
    "run a 7B parameter model at 4-bit",
    "run Docker containers",
    "render 4K video with ffmpeg",
)


def _rule(title: str) -> None:
    print()
    print(f"── {title} " + "─" * max(0, 72 - len(title)))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        home = Path(args[0]).expanduser().resolve()
    else:
        home = Path(tempfile.mkdtemp(prefix="specs-demo-")) / "hermes-home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)

    import specs
    from specs import card, probes, schemas, tools

    settings = specs.current_settings()
    cache = probes.cache_path()
    warm = probes.load_cache()

    print(f"specs {specs.__version__} demo · python {sys.version.split()[0]}")
    print(f"throwaway HERMES_HOME: {home}")
    print(f"cache file: {cache}")
    cache_was_fresh = bool(warm) and not probes.is_stale(warm, settings)
    print(f"cache before this run: {'present, fresh' if cache_was_fresh else 'absent or stale'}")
    print(f"tools registered: {', '.join(schema['name'] for schema in schemas.ALL)}")

    _rule("1. measure (cold or from cache)")
    started = time.perf_counter()
    facts = probes.measure(settings)
    elapsed = time.perf_counter() - started
    statuses = ", ".join(f"{name}={facts['sections'][name]['status']}" for name in probes.SECTIONS)
    print(f"measure() took {elapsed * 1000:.2f} ms → {statuses}")
    print(f"cache written: {cache.is_file()} ({cache.stat().st_size if cache.is_file() else 0} bytes)")

    _rule("2. the capability card (this is what a new session sees)")
    rendered = card.render_card(facts, settings.card_max_chars)
    print(rendered)
    print(f"\n[{len(rendered)}/{settings.card_max_chars} chars]")

    _rule("3. machine_specs(section=\"tools\") — measured values and how")
    tools_sheet = tools.machine_specs({"section": "tools"})
    for line in tools_sheet.splitlines()[:12]:
        print(line)

    _rule("4. machine_can — three plans checked against the measurements")
    for task in DEMO_TASKS:
        print()
        print(tools.machine_can({"task": task}))
        print()

    _rule("5. second measure in the same process (cache path)")
    started = time.perf_counter()
    again = probes.measure(settings)
    warm_elapsed = time.perf_counter() - started
    same = card.render_card(again, settings.card_max_chars) == rendered
    print(f"measure() took {warm_elapsed * 1000:.2f} ms · identical card: {same}")
    if cache_was_fresh:
        print(f"this run started from the cache: {elapsed * 1000:.2f} ms, no probes ran")
    else:
        print(f"cold {elapsed * 1000:.2f} ms vs warm {warm_elapsed * 1000:.2f} ms "
              f"({'cache is doing its job' if warm_elapsed < elapsed else 'NO SPEEDUP'})")

    _rule("6. refresh one section (the manual path)")
    refreshed = tools.machine_refresh({"section": "tools"})
    print("\n".join(refreshed.splitlines()[:4]))
    print("...")

    if cache_was_fresh:
        cache_check = elapsed < 0.05 and cache.is_file()
        cache_label = "this run started from the cache (no probing needed)"
    else:
        cache_check = warm_elapsed < elapsed / 2
        cache_label = "a warm measure is at least 2x faster than a cold one"

    checks = {
        "card within budget": len(rendered) <= settings.card_max_chars,
        "cache file exists": cache.is_file(),
        cache_label: cache_check,
        "card is stable across measures": same,
    }
    _rule("result")
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
