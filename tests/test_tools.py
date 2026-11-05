"""Handler behaviour: useful text for good input, text (never an exception) for bad.

The plugin contract is ``def handler(args, **kwargs) -> str`` with no raising — a
tool call is inside the model's turn, so an exception there costs the turn. These
tests feed the handlers the shapes a model actually produces and the shapes a
fuzzer would.
"""

from __future__ import annotations

import json
import time

import pytest

from specs import probes, tools


@pytest.fixture()
def frozen(monkeypatch, facts):
    built = facts()
    monkeypatch.setattr(probes, "measure", lambda *args, **kwargs: built)
    return built


def test_machine_specs_all_has_every_section_and_provenance(frozen):
    text = tools.machine_specs({"section": "all"})
    for title in ("[System]", "[CPU]", "[Memory]", "[GPU]", "[Disk]", "[Tools]"):
        assert title in text
    assert text.count("obtained by:") >= 10
    assert "every value names how it was obtained" in text
    assert "machine_can(task)" in text


@pytest.mark.parametrize("section", ["system", "cpu", "memory", "gpu", "disk", "tools"])
def test_each_section_renders_alone(frozen, section):
    text = tools.machine_specs({"section": section})
    assert text.startswith(f"machine specs · {section}")
    assert "obtained by:" in text
    assert "[CPU]" not in text or section == "cpu"


def test_machine_specs_defaults_to_all_and_accepts_no_arguments(frozen):
    assert "[CPU]" in tools.machine_specs({})
    assert "[CPU]" in tools.machine_specs(None)
    assert "[CPU]" in tools.machine_specs()


def test_unknown_section_lists_the_valid_ones(frozen):
    text = tools.machine_specs({"section": "network"})
    assert text.startswith("Error: unknown section")
    for name in probes.SECTIONS:
        assert name in text


def test_machine_can_requires_a_task(frozen):
    for args in ({}, None, {"task": "   "}):
        text = tools.machine_can(args)
        assert text.startswith("Error: machine_can needs a task")


def test_machine_can_reports_verdict_and_arithmetic(frozen):
    text = tools.machine_can({"task": "run a 7B model at 4-bit"})
    assert "heuristic verdict, not a guarantee" in text
    assert "verdict: supported" in text
    assert "arithmetic:" in text
    assert "3.85 GB" in text
    assert "measured facts used:" in text
    assert "measured " in text


def test_machine_refresh_rewrites_the_cache_and_reports_the_section(hermes_home, monkeypatch):
    def make(name):
        def probe(_settings):
            return {"status": "measured", "data": {"probe": name}, "evidence": [f"{name} stub"]}
        return probe

    monkeypatch.setattr(probes, "_PROBES", {name: make(name) for name in probes.SECTIONS})
    before = time.time()
    text = tools.machine_refresh({"section": "cpu"})
    assert text.startswith("refreshed cpu in ")
    assert "cache updated on disk" in text
    assert probes.cache_path().is_file()
    stored = json.loads(probes.cache_path().read_text(encoding="utf-8"))
    assert stored["sections"]["cpu"]["data"] == {"probe": "cpu"}
    assert stored["sections"]["cpu"]["measured_epoch"] >= before


def test_machine_refresh_all_and_invalid_section(hermes_home):
    assert "refreshed all" in tools.machine_refresh({"section": "all"})
    assert tools.machine_refresh({"section": "nope"}).startswith("Error: unknown section")


def test_machine_refresh_admits_a_failed_write(hermes_home, monkeypatch):
    monkeypatch.setattr(probes, "save_cache", lambda *args, **kwargs: False)
    text = tools.machine_refresh({"section": "cpu"})
    assert "cache NOT written" in text


HOSTILE_ARGS = [
    None,
    {},
    [],
    "string",
    42,
    {"section": 5},
    {"section": {"nested": "dict"}},
    {"task": object()},
    {"task": "x" * 20000},
    {"task": ["run", "docker"]},
    {"unexpected": True},
]


@pytest.mark.parametrize("handler", [tools.machine_specs, tools.machine_can, tools.machine_refresh])
@pytest.mark.parametrize("args", HOSTILE_ARGS)
def test_handlers_never_raise_and_always_return_text(frozen, handler, args):
    text = handler(args)
    assert isinstance(text, str)
    assert text.strip()


@pytest.mark.parametrize("handler", [tools.machine_specs, tools.machine_can, tools.machine_refresh])
def test_handlers_survive_a_broken_measurement(monkeypatch, handler):
    def boom(*args, **kwargs):
        raise RuntimeError("probe subsystem exploded")

    monkeypatch.setattr(probes, "measure", boom)
    text = handler({"task": "run docker", "section": "all"})
    assert isinstance(text, str)
    assert text.startswith("Error:")
    assert "exploded" not in text  # no raw exception text leaks into the turn


def test_render_specs_of_an_empty_fact_set_still_answers():
    text = tools.render_specs({"sections": {}}, "all")
    assert "no system facts" in text
    assert "unknown" in text.lower()
