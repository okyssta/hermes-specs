"""The card: bounded, informative, deterministic, and free of identifiers.

Core *drops* a system-prompt section that exceeds its ``max_chars``, so an
over-long card disappears silently rather than being clipped. That makes the size
cap a correctness property, not a style preference.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import time
import uuid
from pathlib import Path

import pytest

from specs import card, probes
from specs.settings import Settings


ALL_TOOLS = ("python3", "pip", "uv", "node", "npm", "git", "ffmpeg", "curl", "jq",
             "sqlite3", "tesseract", "docker", "podman", "systemctl", "apt", "dnf",
             "pacman", "brew", "winget")


def test_default_card_says_the_things_a_plan_needs(facts):
    text = card.render_card(facts())
    assert text.startswith("specs · machine capability card")
    assert "os: Linux · 6.1.0-test · x86_64" in text
    assert "cpu: 8 logical cores · Test CPU" in text
    assert "ram: 12.0 GiB available of 16.0 GiB" in text
    assert "gpu: none detected" in text
    assert "disk: 100.0 GiB free of 500.0 GiB" in text
    assert "tools: python3 · git · ffmpeg" in text
    assert "system: systemd (pid 1)" in text
    assert re.search(r"measured \d{4}-\d{2}-\d{2}; call machine_specs for detail$", text)
    assert len(text.splitlines()) <= 9


def test_gpu_lines_distinguish_cuda_vram_and_virtual_adapters(facts):
    nvidia = facts(gpu_vendor="nvidia", compute=True, vram_mb=24564,
                   gpu_devices=[{"name": "NVIDIA GeForce RTX 4090", "vram_mb": 24564}])
    assert "gpu: NVIDIA GeForce RTX 4090 · 24 GB VRAM · CUDA" in card.render_card(nvidia)

    virtual = facts(gpu_vendor="virtual", compute=False,
                    gpu_devices=[{"name": "Red Hat, Inc. Virtio 1.0 GPU", "vram_mb": None}])
    assert "gpu: virtual display only (no CUDA/ROCm)" in card.render_card(virtual)

    driverless = facts(gpu_vendor="nvidia", compute=False,
                       gpu_devices=[{"name": "NVIDIA GeForce RTX 3090", "vram_mb": None}])
    assert "driver unusable" in card.render_card(driverless)


def test_absent_tools_are_named_because_plans_assume_them(facts):
    text = card.render_card(facts(present=("python3",)))
    assert "no docker" in text
    assert "ffmpeg" in text  # named in the absent clause


def test_unknown_sections_say_unknown_instead_of_inventing_values(facts):
    poisoned = facts()
    poisoned["sections"]["memory"] = {"status": "unknown", "data": {}, "evidence": [],
                                      "measured_epoch": time.time()}
    text = card.render_card(poisoned)
    assert "ram: unknown" in text
    assert "GiB available" not in text


@pytest.mark.parametrize("cap", [1200, 900, 600, 400, 300, 200, 150, 120])
def test_card_never_exceeds_the_cap(facts, cap):
    text = card.render_card(facts(present=ALL_TOOLS), cap)
    assert len(text) <= cap, (cap, len(text))


@pytest.mark.parametrize("cap", [80, 60, 40])
def test_tiny_caps_still_return_a_string_within_budget(facts, cap):
    text = card.render_card(facts(present=ALL_TOOLS), cap)
    assert isinstance(text, str)
    assert 0 < len(text) <= cap


def test_the_footer_survives_normal_caps(facts):
    for cap in (1200, 600, 300, 200):
        text = card.render_card(facts(present=ALL_TOOLS), cap)
        assert "machine_specs" in text, cap


def test_a_tight_budget_drops_tools_first_not_the_hardware(facts):
    roomy = card.render_card(facts(present=ALL_TOOLS), 1200)
    tight = card.render_card(facts(present=ALL_TOOLS), 400)
    assert "ram:" in tight and "cpu:" in tight
    assert len(tight) < len(roomy)


def test_tools_line_counts_what_it_omitted():
    line = card._tools_line(["python3 3.12.4"], 18, ["docker"])
    assert "+18 more" in line
    assert "no docker" in line


def test_rendering_is_deterministic(facts):
    frozen = facts(epoch=1_760_000_000)
    assert card.render_card(frozen, 1200) == card.render_card(frozen, 1200)
    assert "2025-10-09" in card.render_card(frozen)  # the frozen epoch, not today


def test_render_never_raises_on_garbage():
    for payload in (None, {}, {"sections": "nope"}, {"sections": {"gpu": 5}},
                    {"sections": {"memory": {"data": {"total_bytes": "lots"}}}}):
        text = card.render_card(payload, 1200)
        assert isinstance(text, str) and 0 < len(text) <= 1200


# ───────────────────────────────────────────────────────────────── privacy


def test_poisoned_facts_are_scrubbed_before_they_reach_the_prompt(facts):
    poisoned = facts(
        gpu_vendor="nvidia",
        compute=True,
        vram_mb=24564,
        gpu_devices=[{"name": "RTX 4090 /home/alice/secret 10.1.2.3 aa:bb:cc:dd:ee:ff"},
                     {"name": "bob@example.com GPU"}],
        model="Xeon /Users/alice/Projects/cpu",
        kernel="6.1.0-hostname",
    )
    text = card.render_card(poisoned, 1200)
    for leaked in ("alice", "10.1.2.3", "aa:bb:cc:dd:ee:ff", "bob@example.com", "/home", "/Users"):
        assert leaked not in text, leaked
    assert "(path)" in text or "(redacted)" in text


def test_scrub_removes_paths_addresses_and_emails():
    dirty = ("user alice ran /home/alice/tool from 192.168.1.10 on "
             "de:ad:be:ef:00:01 as alice@example.com")
    clean = card.scrub(dirty)
    for leaked in ("/home/alice", "192.168.1.10", "de:ad:be:ef:00:01", "alice@example.com"):
        assert leaked not in clean
    assert "(path)" in clean and "(redacted)" in clean


def test_the_card_from_this_real_machine_carries_no_identifier(hermes_home):
    facts = probes.measure(Settings())
    text = card.render_card(facts, 1200)
    user = getpass.getuser()
    hostname = socket.gethostname()
    home = str(Path.home())
    mac_hex = f"{uuid.getnode():012x}"

    assert user and user not in text, f"username leaked: {user}"
    assert hostname and hostname not in text, f"hostname leaked: {hostname}"
    if home not in ("/", ""):
        assert home not in text, f"home path leaked: {home}"
    assert mac_hex not in text.lower().replace(":", ""), "MAC address leaked"
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text), "IPv4 address leaked"
    assert os.environ.get("USER", "zzz-not-set") not in text
    assert os.environ.get("HOSTNAME", "zzz-not-set") not in text


def test_the_real_card_stays_within_the_configured_budget(hermes_home):
    facts = probes.measure(Settings())
    for cap in (1200, 600, 300):
        assert len(card.render_card(facts, cap)) <= cap
