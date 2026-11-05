"""Shared harness: a throwaway HERMES_HOME and a synthetic-facts builder.

Tests never read the developer's real cache and never assume facts about the host
running them — every assertion about rendering or arithmetic starts from
``facts()``, a capability dict built from explicit numbers. The probe tests mock
the two primitives probes.py uses (``_read_text`` and ``_run``) so they are
deterministic on Linux, macOS and Windows alike.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_SRC = Path(os.environ.get("HERMES_SRC", "/root/.hermes/hermes-agent"))


def pytest_configure(config):
    for path in (str(REPO_ROOT.parent), str(HERMES_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


@pytest.fixture(autouse=True)
def _reset_plugin_state():
    """Every test starts from a cold plugin: no memoised settings, no memoised card."""
    import specs as plugin

    plugin._CTX = None
    plugin._CACHED = None
    plugin._CACHED_AT = 0.0
    plugin.invalidate_card()
    yield
    plugin._CTX = None
    plugin._CACHED = None
    plugin._CACHED_AT = 0.0
    plugin.invalidate_card()


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    """A fresh profile dir, so the cache and plugin-data land somewhere disposable."""
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home


@pytest.fixture()
def settings():
    from specs.settings import Settings

    return Settings()


def _section(data: dict[str, Any], evidence: list[str] | None = None, epoch: float | None = None,
             status: str = "measured") -> dict[str, Any]:
    return {
        "status": status,
        "data": data,
        "evidence": evidence or ["mocked probe"],
        "measured_epoch": time.time() if epoch is None else epoch,
    }


def build_facts(**overrides: Any) -> dict[str, Any]:
    """Build a complete facts dict from explicit numbers.

    Any keyword whose name matches a section replaces that section's ``data`` with
    a dict merged over the defaults; ``epoch`` shifts every measured timestamp;
    ``section_status`` overrides one section's status.
    """
    epoch = overrides.pop("epoch", None)
    section_status = overrides.pop("section_status", {}) or {}
    gpu = {
        "vendor": overrides.pop("gpu_vendor", "none"),
        "devices": overrides.pop("gpu_devices", []),
        "vram_mb": overrides.pop("vram_mb", None),
        "compute": overrides.pop("compute", False),
    }
    gpu_note = overrides.pop("gpu_note", None)
    if gpu_note:
        gpu["note"] = gpu_note
    present = overrides.pop("present", ("python3", "git", "ffmpeg"))
    versions = overrides.pop("versions", {}) or {}
    browsers = overrides.pop("browsers", ()) or ()
    docker_binary = overrides.pop("docker_binary", False)
    daemon_status = overrides.pop("daemon_status", "absent")

    items = {}
    for name in ("python3", "pip", "uv", "node", "npm", "git", "ffmpeg", "curl", "jq",
                 "sqlite3", "tesseract", "docker", "podman", "systemctl", "apt", "dnf",
                 "pacman", "brew", "winget"):
        items[name] = {
            "present": name in present or (name == "docker" and docker_binary),
            "version": versions.get(name) if name in present else None,
            "source": f"{name} --version",
        }
    data: dict[str, Any] = {
        "system": {
            "os": overrides.pop("os_name", "Linux"),
            "kernel": overrides.pop("kernel", "6.1.0-test"),
            "arch": overrides.pop("arch", "x86_64"),
            "python": "3.12.0",
            "init": overrides.pop("init", "systemd"),
            "container": overrides.pop("container", None),
            "elevated": overrides.pop("elevated", True),
            "shells": ["bash", "sh"],
        },
        "cpu": {
            "cores": overrides.pop("cores", 8),
            "model": overrides.pop("model", "Test CPU"),
            "arch": overrides.pop("arch", "x86_64"),
        },
        "memory": {
            "total_bytes": overrides.pop("total", 16 * 1024 ** 3),
            "available_bytes": overrides.pop("available", 12 * 1024 ** 3),
            "estimate": False,
        },
        "gpu": gpu,
        "disk": {
            "free_bytes": overrides.pop("free", 100 * 1024 ** 3),
            "total_bytes": overrides.pop("disk_total", 500 * 1024 ** 3),
            "label": "hermes data volume",
        },
        "tools": {
            "items": items,
            "browsers": {name: {"present": name in browsers, "source": "shutil.which()"}
                         for name in ("chromium", "chromium-browser", "google-chrome",
                                      "google-chrome-stable", "chrome")},
            "docker": {
                "binary": docker_binary,
                "daemon": {"status": daemon_status, "server": None, "source": "docker info"},
            },
            "present_count": sum(1 for i in items.values() if i["present"]),
            "measured_count": len(items),
        },
    }
    for name in ("system", "cpu", "memory", "gpu", "disk", "tools"):
        extra = overrides.pop(name, None)
        if isinstance(extra, dict):
            data[name] = {**data[name], **extra}
    sections = {
        name: _section(payload, status=section_status.get(name, "measured"), epoch=epoch)
        for name, payload in data.items()
    }
    return {"schema": 1, "sections": sections}


@pytest.fixture()
def facts():
    return build_facts


class RecordingContext:
    """A minimal plugin host: records what register() asks for."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.plugin_id = "specs"
        self.tools: list[dict[str, Any]] = []
        self.commands: list[tuple[str, Any]] = []
        self.sections: list[tuple[str, Any, dict[str, Any]]] = []
        self.skills: list[tuple[str, str]] = []

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_command(self, name, handler, **kwargs):
        self.commands.append((name, handler))

    def register_system_prompt_section(self, section_id, content, **kwargs):
        self.sections.append((section_id, content, kwargs))

    def register_skill(self, name, path):
        self.skills.append((name, path))


@pytest.fixture()
def recorder():
    return RecordingContext()


@pytest.fixture()
def context_factory():
    """Build a recording host with a specific config, e.g. ``context_factory({"enabled": False})``."""
    return RecordingContext
