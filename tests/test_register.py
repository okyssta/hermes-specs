"""Registering the plugin: the surfaces, the section budget, and load-time cost.

``register()`` runs inside Hermes' startup (and inside the admission probe with a
30-second budget), so it must be cheap and must never probe the machine. The
expensive measurement happens when the card is first rendered or a tool is called.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

import specs as plugin
from specs import probes, schemas
from specs.settings import SCHEMA_KEYS


def test_register_exposes_exactly_three_tools_and_one_command(recorder):
    plugin.register(recorder)
    names = [tool["name"] for tool in recorder.tools]
    assert names == ["machine_specs", "machine_can", "machine_refresh"]
    assert names == [schema["name"] for schema in schemas.ALL]
    assert {tool["toolset"] for tool in recorder.tools} == {"specs"}
    for tool in recorder.tools:
        assert callable(tool["handler"])
        assert tool["schema"]["parameters"]["type"] == "object"
    assert [name for name, _handler in recorder.commands] == ["specs"]


def test_register_adds_the_prompt_section_with_the_configured_budget(recorder):
    plugin.register(recorder)
    assert len(recorder.sections) == 1
    section_id, content, kwargs = recorder.sections[0]
    assert section_id == "specs.card"
    assert callable(content)
    assert kwargs["position"] == "after_memory"
    assert kwargs["max_chars"] == 1200
    assert kwargs["max_chars"] <= 4000  # core's hard ceiling


def test_register_bundles_the_skill(recorder):
    plugin.register(recorder)
    assert recorder.skills
    name, path = recorder.skills[0]
    assert name == "specs"
    assert (Path(path) / "SKILL.md").is_file()


def test_register_does_probe_at_load_time(frozen_measure, recorder):
    plugin.register(recorder)
    assert frozen_measure == [], "register() must not measure the machine"


@pytest.fixture()
def frozen_measure(monkeypatch):
    calls: list[tuple] = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return {"schema": 1, "sections": {}}

    monkeypatch.setattr(probes, "measure", spy)
    return calls


def test_disabled_plugin_registers_tools_but_no_card(context_factory):
    ctx = context_factory({"enabled": False})
    plugin.register(ctx)
    assert len(ctx.tools) == 3
    assert ctx.sections == []


def test_card_section_accepts_a_read_only_mapping(hermes_home):
    text = plugin.card_section(MappingProxyType({"session_id": "abc"}))
    assert isinstance(text, str)
    assert "machine_specs" in text
    assert "abc" not in text, "session identifiers must not be copied into the card"


def test_card_section_never_raises_when_measurement_fails(monkeypatch):
    monkeypatch.setattr(probes, "measure", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    assert plugin.card_section({}) == ""


def test_card_section_is_rendered_once_per_process(monkeypatch, facts):
    calls: list[int] = []

    def fake_measure(*args, **kwargs):
        calls.append(1)
        return facts()

    monkeypatch.setattr(probes, "measure", fake_measure)
    plugin.invalidate_card()
    first = plugin.card_section({})
    second = plugin.card_section({})
    assert first == second
    assert len(calls) == 1
    plugin.invalidate_card()
    plugin.card_section({})
    assert len(calls) == 2


def test_invalidate_card_is_used_by_refresh(hermes_home, monkeypatch, facts):
    monkeypatch.setattr(probes, "_PROBES",
                        {name: (lambda _s, n=name: {"status": "measured", "data": {}, "evidence": []})
                         for name in probes.SECTIONS})
    plugin.invalidate_card()
    plugin.card_section({})
    plugin.invalidate_card()
    from specs import tools

    tools.machine_refresh({"section": "cpu"})
    assert plugin._CARD_MEMO["text"] == ""


# ─── through the real core manager, not a stub ──────────────────────────────


def test_core_renders_the_section_within_budget(hermes_home):
    """Through the real PluginManager — skipped only where Hermes' deps are absent."""
    pytest.importorskip("ruamel.yaml", reason="hermes_cli needs ruamel; scripts/verify_prompt_section.py covers this")
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    manager = PluginManager()
    ctx = PluginContext(PluginManifest(name="specs", key="specs", source="user"), manager)
    plugin.register(ctx)

    section = manager._system_prompt_sections["specs.card"]
    assert section.position == "after_memory"
    assert section.max_chars == 1200

    rendered = manager.render_system_prompt_sections({"session_id": "session-1"})
    assert [item.id for item in rendered] == ["specs.card"]
    content = rendered[0].content
    assert len(content) <= 1200
    assert "machine_specs" in content
    assert "ram:" in content


def test_unknown_config_keys_do_not_break_settings(context_factory):
    ctx = context_factory({key: "not-the-right-type" for key in SCHEMA_KEYS})
    ctx.config.update({"ttl_hours": "abc", "card_max_chars": 99999, "probe_timeout_ms": -5,
                       "tools": "git, python3"})
    settings = plugin.Settings.from_ctx(ctx)
    assert settings.ttl_hours == 24
    assert settings.card_max_chars == 4000
    assert settings.probe_timeout_ms == 100
    assert settings.tools == ("git", "python3")
