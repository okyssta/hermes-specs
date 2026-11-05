"""/specs behaves like a person-facing command: prose, no JSON, no tracebacks."""

from __future__ import annotations

import pytest

from specs import commands, probes


@pytest.fixture()
def frozen(monkeypatch, facts):
    built = facts()
    monkeypatch.setattr(probes, "measure", lambda *args, **kwargs: built)
    return built


def test_bare_specs_prints_the_card(frozen, hermes_home):
    text = commands.handle("")
    assert text.startswith("specs · machine capability card")
    assert "machine_specs" in text


def test_card_shows_where_it_is_cached(frozen, hermes_home):
    text = commands.handle("card")
    assert "cache:" in text
    assert "plugin-data/specs/specs.json" in text
    assert "machine capability card" in text


def test_status_reports_cache_and_settings(frozen, hermes_home):
    text = commands.handle("status")
    assert "cache:" in text
    assert "card cap: 1200 chars" in text
    assert "cache TTL: 24h" in text


def test_help_lists_the_surfaces(frozen, hermes_home):
    text = commands.handle("help")
    for fragment in ("/specs card", "/specs refresh", "/specs can", "/specs status"):
        assert fragment in text


def test_can_runs_a_feasibility_check(frozen, hermes_home):
    text = commands.handle("can run a 7B model at 4-bit")
    assert "verdict: supported" in text
    assert "heuristic" in text


def test_section_names_print_that_section(frozen, hermes_home):
    text = commands.handle("gpu")
    assert text.startswith("machine specs · gpu")
    assert "vendor" in text


def test_refresh_reports_what_it_reprobed(frozen, hermes_home):
    assert commands.handle("refresh cpu").startswith("refreshed cpu in ")


def test_refresh_rejects_an_unknown_section(frozen, hermes_home):
    text = commands.handle("refresh network")
    assert text.startswith("Unknown section")
    assert "memory" in text


def test_can_without_a_task_explains_the_usage(frozen, hermes_home):
    assert commands.handle("can").startswith("Usage: /specs can")


def test_unknown_subcommand_helps_instead_of_failing(frozen, hermes_home):
    text = commands.handle("frobnicate")
    assert "Unknown subcommand" in text
    assert "/specs" in text


@pytest.mark.parametrize("raw", [None, 5, "", "  ", "x" * 5000, {"a": 1}])
def test_command_never_raises(frozen, hermes_home, raw):
    text = commands.handle(raw)
    assert isinstance(text, str) and text.strip()
