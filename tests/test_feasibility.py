"""Feasibility arithmetic, tested on fixed numbers rather than on this host.

Each case builds the machine it needs, so the expected verdict follows from the
formula the docstring states: weights = params × bytes/param, overhead = 25% of
weights + 0.5 GB, capacity = 90% of available RAM (or 90% of VRAM).
"""

from __future__ import annotations

import pytest

from specs import tools


def verdict(facts: dict, task: str) -> dict:
    return tools.assess(task, facts)


# ────────────────────────────────────────────────────────────── local models


def test_7b_at_4bit_fits_in_8_gib(facts):
    result = verdict(facts(total=16 * 1024 ** 3, available=8 * 1024 ** 3),
                     "run a 7B parameter model at 4-bit")
    assert result["verdict"] == "supported"
    assert result["kind"] == "model"
    arithmetic = " ".join(result["arithmetic"])
    assert "3.85 GB" in arithmetic          # 7e9 * 0.55
    assert "5.35 GB" in arithmetic          # weights + 1.50 GB overhead
    assert "capacity" in arithmetic


def test_70b_at_fp16_does_not_fit_in_8_gib(facts):
    result = verdict(facts(total=16 * 1024 ** 3, available=8 * 1024 ** 3),
                     "run a 70B model at fp16")
    assert result["verdict"] == "not supported"
    assert "short by" in result["why"]


def test_7b_at_4bit_does_not_fit_in_2_gib(facts):
    result = verdict(facts(total=4 * 1024 ** 3, available=2 * 1024 ** 3),
                     "run a 7B model at 4-bit")
    assert result["verdict"] == "not supported"


def test_a_model_at_the_capacity_edge_is_uncertain_not_a_guess(facts):
    # need 5.35 GB; capacity between need/1.2 and need/0.95 → uncertain
    available = (5.0e9 / 0.9)
    result = verdict(facts(total=8e9, available=available), "run a 7B model at 4-bit")
    assert result["verdict"] == "uncertain"
    assert "edge" in result["why"]


def test_a_model_without_a_size_is_uncertain_and_asks_for_one(facts):
    result = verdict(facts(), "run a large language model locally")
    assert result["kind"] == "model"
    assert result["verdict"] == "uncertain"
    assert any("parameter count" in line for line in result["arithmetic"])
    assert any("13B" in caveat or "size" in caveat for caveat in result["caveats"])


def test_quantization_spellings_are_understood(facts):
    for task, expected in (
        ("run Qwen 2.5 7B at q4_K_M", "0.550"),
        ("run a 7B model in 8-bit", "1.100"),
        ("run a 7B model at fp16", "2.000"),
        ("run mistral 7b gguf", "4-bit (assumed"),
    ):
        result = verdict(facts(total=64e9, available=64e9), task)
        joined = " ".join(result["arithmetic"])
        assert expected in joined, (task, joined)


def test_cpu_only_verdict_carries_a_speed_caveat(facts):
    result = verdict(facts(available=8 * 1024 ** 3, gpu_vendor="virtual", compute=False),
                     "run a 7B model at 4-bit")
    assert any("tokens/s" in caveat for caveat in result["caveats"])
    assert any("CPU core" in caveat for caveat in result["caveats"])


def test_gpu_vram_is_used_as_capacity_when_a_compute_gpu_is_present(facts):
    result = verdict(
        facts(available=4 * 1024 ** 3, gpu_vendor="nvidia", compute=True, vram_mb=24564),
        "run a 13B model at 4-bit",
    )
    assert result["verdict"] == "supported"
    assert any("VRAM" in line for line in result["arithmetic"])
    assert not any("CPU core" in caveat for caveat in result["caveats"])


# ──────────────────────────────────────────────────────────────────── docker


def test_docker_needs_both_binary_and_daemon(facts):
    ready = verdict(facts(docker_binary=True, daemon_status="reachable"), "run Docker containers")
    assert ready["verdict"] == "supported"

    no_binary = verdict(facts(docker_binary=False), "run Docker containers")
    assert no_binary["verdict"] == "not supported"
    assert "no docker binary" in no_binary["why"]

    no_daemon = verdict(facts(docker_binary=True, daemon_status="unreachable"), "run Docker containers")
    assert no_daemon["verdict"] == "not supported"
    assert "no daemon" in no_daemon["why"]

    unclear = verdict(facts(docker_binary=True, daemon_status="unknown"), "run Docker containers")
    assert unclear["verdict"] == "uncertain"


# ────────────────────────────────────────────────────────────────────── cuda


def test_cuda_requires_a_compute_capable_nvidia_device(facts):
    assert verdict(facts(gpu_vendor="nvidia", compute=True, vram_mb=8192),
                   "train a model with CUDA")["verdict"] == "supported"
    assert verdict(facts(gpu_vendor="none", compute=False),
                   "train a model with CUDA")["verdict"] == "not supported"
    driverless = verdict(facts(gpu_vendor="nvidia", compute=False,
                               gpu_note="driver absent"),
                         "use CUDA")
    assert driverless["verdict"] == "not supported"
    assert "driver" in driverless["why"]


# ─────────────────────────────────────────────────────────────────── browser


def test_browser_needs_a_real_binary_or_a_way_to_install_one(facts):
    assert verdict(facts(browsers=("chromium",)), "run a headless browser")["verdict"] == "supported"
    assert verdict(facts(browsers=(), present=("node", "npm")),
                   "run a headless browser")["verdict"] == "uncertain"
    assert verdict(facts(browsers=(), present=("git",)),
                   "run a headless browser")["verdict"] == "not supported"


# ──────────────────────────────────────────────────────────────────── video


def test_video_needs_ffmpeg_and_disk_space(facts):
    assert verdict(facts(present=("git",)), "render 4K video with ffmpeg")["verdict"] == "not supported"

    no_room = verdict(facts(present=("ffmpeg",), free=200 * 1024 ** 2),
                      "render 4K video with ffmpeg")
    assert no_room["verdict"] == "not supported"
    assert "would not fit" in no_room["why"]

    tight = verdict(facts(present=("ffmpeg",), free=int(0.9e9)),
                    "render 4K video with ffmpeg")
    assert tight["verdict"] == "uncertain"

    roomy = verdict(facts(present=("ffmpeg",), free=50 * 1024 ** 3),
                    "render 4K video with ffmpeg")
    assert roomy["verdict"] == "supported"
    assert any("software encode" in caveat for caveat in roomy["caveats"])


def test_video_duration_changes_the_estimate(facts):
    short = verdict(facts(present=("ffmpeg",), free=50 * 1024 ** 3),
                    "encode a 30 second 1080p clip with ffmpeg")
    long = verdict(facts(present=("ffmpeg",), free=50 * 1024 ** 3),
                   "encode a 1 hour 1080p clip with ffmpeg")
    short_line = [line for line in short["arithmetic"] if "estimate:" in line][0]
    long_line = [line for line in long["arithmetic"] if "estimate:" in line][0]
    assert "for 30s" in short_line or "for 30s" in short_line.replace(".0", "")
    assert "3600s" in long_line


# ─────────────────────────────────────────────────────────────────── systemd


def test_systemd_verdict_follows_pid_one(facts):
    assert verdict(facts(init="systemd", present=("systemctl",)),
                   "install a systemd service")["verdict"] == "supported"
    other = verdict(facts(init="openrc", present=("systemctl",)),
                    "install a systemd service")
    assert other["verdict"] == "not supported"
    assert "openrc" in other["why"]


# ──────────────────────────────────────────────────────────────── fallback


def test_an_unrecognised_task_is_uncertain_and_admits_it(facts):
    result = verdict(facts(), "make me a sandwich")
    assert result["kind"] == "unknown"
    assert result["verdict"] == "uncertain"
    assert "no feasibility rule matched" in result["why"]


def test_classification_orders_video_before_generic_model_words(facts):
    result = verdict(facts(present=("ffmpeg",), free=50 * 1024 ** 3),
                     "render a 4K video with ffmpeg")
    assert result["kind"] == "video"


def test_every_rendered_verdict_is_labelled_a_heuristic(facts):
    for task in ("run a 7B model at 4-bit", "run Docker containers", "make me a sandwich"):
        text = tools.render_verdict(task, tools.assess(task, facts()), facts())
        assert "heuristic" in text
        assert "not a guarantee" in text
        assert text.startswith("machine_can(")
