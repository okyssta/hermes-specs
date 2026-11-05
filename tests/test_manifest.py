"""The manifest must describe exactly what the code does.

Catalog admission compares ``provides_tools`` with the tools ``register()`` really
registers, and the Desktop settings form reads ``config_schema``; a key that exists
in one place and not the other is a silent bug. These tests are the contract.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def manifest() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((REPO / "plugin.yaml").read_text(encoding="utf-8"))


def test_identity_and_versions(manifest):
    assert manifest["name"] == "specs"
    assert manifest["label"] == "Specs"
    assert manifest["version"] == "1.0.0"
    assert manifest["manifest_version"] == 2
    assert manifest["api_version"] == 1
    assert manifest["requires_hermes"] == ">=0.22.0"
    assert manifest["license"] == "MIT"
    assert manifest["author"] == "kyssta"
    assert manifest["homepage"] == "https://github.com/okyssta/hermes-specs"
    assert manifest["title"]


def test_description_ends_with_a_disclosure_sentence(manifest):
    description = " ".join(str(manifest["description"]).split())
    assert "Disclosure —" in description
    tail = description[description.rindex("Disclosure —"):]
    assert tail.endswith("telemetry."), tail[-80:]
    # The disclosure must name what the plugin touches and what it refuses to do.
    for phrase in ("read-only local probes", "plugin-data/specs/", "no network calls"):
        assert phrase in tail, phrase


def test_provides_tools_is_exactly_what_register_registers(manifest):
    from specs import schemas

    declared = manifest["provides_tools"]
    assert declared == [schema["name"] for schema in schemas.ALL]
    assert set(declared) == {"machine_specs", "machine_can", "machine_refresh"}


def test_no_hooks_are_declared_or_needed(manifest):
    assert manifest.get("provides_hooks") == []


def test_config_schema_keys_match_settings_one_for_one(manifest):
    from specs.settings import SCHEMA_KEYS

    assert tuple(manifest["config_schema"].keys()) == tuple(SCHEMA_KEYS)


def test_config_schema_defaults_match_the_settings_dataclass(manifest):
    from specs.settings import Settings

    fields = {field.name: field for field in dataclasses.fields(Settings)}
    type_map = {bool: "bool", int: "int", tuple: "list"}
    for key, spec in manifest["config_schema"].items():
        assert key in fields, key
        default = fields[key].default
        if isinstance(default, tuple):
            assert list(spec["default"]) == list(default), key
        else:
            assert spec["default"] == default, key
        assert spec["type"] == type_map[type(default)], key
        assert spec.get("label") and spec.get("description"), key


def test_settings_defaults_are_sane():
    from specs.settings import Settings

    defaults = Settings()
    assert defaults.enabled is True
    assert defaults.ttl_hours == 24
    assert defaults.card_max_chars == 1200
    assert defaults.probe_timeout_ms == 1500
    assert 0.1 <= defaults.timeout_seconds <= 10
    assert "docker" in defaults.tools and "python3" in defaults.tools
    assert defaults.ttl_seconds == 24 * 3600


def test_bundled_skill_is_present_and_shaped():
    skill = REPO / "skills" / "specs" / "SKILL.md"
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: specs" in text
    assert "machine_specs" in text and "machine_can" in text
    # The skill's job is to stop the agent guessing: it must say so.
    assert "measured" in text.lower()
    assert "uncertain" in text


def test_readme_and_catalog_entry_exist():
    yaml = pytest.importorskip("yaml")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "machine_can" in readme and "Disclosure" in readme
    catalog = yaml.safe_load((REPO / "catalog-entry.yaml").read_text(encoding="utf-8"))
    assert catalog["name"] == "specs"
    assert catalog["repo"] == "https://github.com/okyssta/hermes-specs"
    stored = (REPO / "catalog-entry.yaml").read_text(encoding="utf-8")
    assert "# sha:" in stored, "the sha line must be present and commented as pending"
