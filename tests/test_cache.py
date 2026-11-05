"""Cache behaviour: round-trip, TTL, per-section refresh, corruption, write failure.

The cache is the difference between a session that starts instantly and one that
spawns twenty subprocesses before the first token; it is also the file most likely
to be truncated by a crash. Both properties are tested here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from specs import probes
from specs.settings import Settings


@pytest.fixture()
def stub(monkeypatch):
    """Replace every probe with a counting stub, so 'was it re-probed?' is observable."""
    calls: list[str] = []

    def make(name: str):
        def probe(_settings):
            calls.append(name)
            return {"status": "measured", "data": {"probe": name}, "evidence": [f"{name} stub"]}
        return probe

    monkeypatch.setattr(probes, "_PROBES", {name: make(name) for name in probes.SECTIONS})
    return calls


def _age_sections(path: Path, seconds: float, names: tuple[str, ...] = probes.SECTIONS) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for name in names:
        data["sections"][name]["measured_epoch"] = time.time() - seconds
    path.write_text(json.dumps(data), encoding="utf-8")


def test_cache_round_trips_through_disk(hermes_home):
    facts = probes.measure(Settings(), use_cache=False)
    assert probes.cache_path().is_file()
    loaded = probes.load_cache()
    assert loaded is not None
    assert set(loaded["sections"]) == set(probes.SECTIONS)
    assert loaded["schema"] == probes.SCHEMA_VERSION
    # No identifier fields are written either: the cache feeds a prompt path.
    raw = probes.cache_path().read_text(encoding="utf-8")
    assert '"username"' not in raw and '"hostname"' not in raw
    _ = facts


def test_fresh_cache_is_used_without_probing(hermes_home, stub):
    probes.measure(Settings())
    first = list(stub)
    stub.clear()
    facts = probes.measure(Settings())
    assert stub == [], "a fresh cache must not re-probe"
    assert set(facts["sections"]) == set(probes.SECTIONS)
    assert first == list(probes.SECTIONS)


def test_only_the_stale_section_is_re_probed(hermes_home, stub):
    probes.measure(Settings())
    _age_sections(probes.cache_path(), 2 * 3600, ("cpu",))
    stub.clear()
    probes.measure(Settings(ttl_hours=1))
    assert stub == ["cpu"]


def test_ttl_boundary_re_probes_everything(hermes_home, stub):
    probes.measure(Settings())
    _age_sections(probes.cache_path(), 2 * 3600)
    stub.clear()
    probes.measure(Settings(ttl_hours=1))
    assert stub == list(probes.SECTIONS)


def test_force_re_probes_named_sections_only(hermes_home, stub):
    probes.measure(Settings())
    stub.clear()
    probes.measure(Settings(), force=("gpu",))
    assert stub == ["gpu"]


def test_force_all_re_probes_everything(hermes_home, stub):
    probes.measure(Settings())
    stub.clear()
    probes.measure(Settings(), force=("all",))
    assert stub == list(probes.SECTIONS)


def test_use_cache_false_ignores_and_rewrites(hermes_home, stub):
    probes.measure(Settings())
    stub.clear()
    probes.measure(Settings(), use_cache=False)
    assert stub == list(probes.SECTIONS)


def test_corrupt_cache_falls_back_to_live_probing(hermes_home, stub):
    path = probes.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema": 1, "sections": {', encoding="utf-8")
    assert probes.load_cache() is None
    facts = probes.measure(Settings())
    assert len(stub) == len(probes.SECTIONS)
    assert set(facts["sections"]) == set(probes.SECTIONS)
    assert probes.load_cache() is not None, "a corrupt cache must be replaced by a valid one"


def test_wrong_schema_version_is_ignored(hermes_home, stub):
    path = probes.cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    facts = probes.measure(Settings())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    assert probes.load_cache() is None
    stub.clear()
    probes.measure(Settings())
    assert stub == list(probes.SECTIONS)


def test_a_damaged_section_is_dropped_and_re_probed_alone(hermes_home, stub):
    probes.measure(Settings())
    path = probes.cache_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["sections"]["cpu"]["data"]
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = probes.load_cache()
    assert loaded is not None and "cpu" not in loaded["sections"]
    stub.clear()
    probes.measure(Settings())
    assert stub == ["cpu"]


def test_extra_unknown_sections_are_ignored(hermes_home, stub):
    probes.measure(Settings())
    path = probes.cache_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sections"]["network"] = {"data": {"ip": "10.0.0.5"}, "measured_epoch": time.time()}
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = probes.load_cache()
    assert "network" not in loaded["sections"]


def test_write_failure_is_reported_not_raised(hermes_home, monkeypatch):
    def boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    assert probes.save_cache({"sections": {}}) is False


def test_load_missing_cache_is_none(hermes_home):
    assert probes.load_cache() is None


def test_measured_epoch_is_the_oldest_section(hermes_home, stub):
    facts = probes.measure(Settings())
    _age_sections(probes.cache_path(), 7200, ("disk",))
    reloaded = probes.load_cache()
    assert time.time() - probes.measured_epoch(reloaded) >= 7100
    assert probes.is_stale(reloaded, Settings(ttl_hours=24)) is False
    assert probes.is_stale(reloaded, Settings(ttl_hours=1)) is True
    _ = facts
