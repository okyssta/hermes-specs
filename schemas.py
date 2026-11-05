"""Tool schemas.

The descriptions are written for a model that has never heard of this plugin:
they say what is measured, when quoting a measurement beats guessing, and — for
``machine_can`` — that the answer is a labelled heuristic rather than a promise.
"""

from __future__ import annotations

_SECTION_ENUM = ["all", "system", "cpu", "memory", "gpu", "disk", "tools"]

MACHINE_SPECS = {
    "name": "machine_specs",
    "description": (
        "The measured facts about the machine Hermes is running on: OS, kernel and architecture, CPU "
        "count and model, total and available RAM, GPU (vendor, devices, usable VRAM), free disk, the "
        "init system and container status, and which developer tools are installed with their versions. "
        "Every value names how it was obtained, so it can be quoted with confidence. Call this before "
        "writing a plan that assumes hardware, before installing anything heavy, or whenever the user "
        "asks what this machine has or can run. Values come from a cache measured within the last day; "
        "use machine_refresh when the machine may have changed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": _SECTION_ENUM,
                "description": (
                    "'all' (default) for the whole fact sheet, or one section: system (OS, kernel, arch, "
                    "init, container, privileges, shells), cpu, memory, gpu, disk, tools."
                ),
            }
        },
        "required": [],
    },
}

MACHINE_CAN = {
    "name": "machine_can",
    "description": (
        "A heuristic feasibility check for a task on THIS machine: whether a local LLM of a given size "
        "and quantization fits in RAM/VRAM, whether Docker containers can run, whether CUDA is usable, "
        "whether a headless browser or ffmpeg video encode is possible, whether systemd services can be "
        "managed. Returns supported / not supported / uncertain plus the measured facts and the "
        "arithmetic it used (e.g. 7B at 4-bit ≈ 3.85 GB of weights + overhead versus available RAM). "
        "It is explicitly a heuristic, not a guarantee: treat 'uncertain' as a prompt to read "
        "machine_specs and decide, and never upgrade a verdict to certainty when reporting to the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "The task in plain words, including the numbers that matter, e.g. 'run a 7B "
                    "parameter model at 4-bit', 'run Docker containers', 'render 4K video with ffmpeg', "
                    "'train a model with CUDA', 'run a headless browser'."
                ),
            }
        },
        "required": ["task"],
    },
}

MACHINE_REFRESH = {
    "name": "machine_refresh",
    "description": (
        "Re-measure this machine now and update the cached capability card, because something changed: "
        "software was installed or removed, a GPU driver appeared, RAM was added, disk was freed. "
        "Re-probes one section or all of them, writes the cache, and returns the fresh fact sheet. "
        "Use it after installing a tool the user asked about, then report the new verdict rather than "
        "the stale one."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": _SECTION_ENUM,
                "description": "'all' to re-probe everything (default), or one section name.",
            }
        },
        "required": [],
    },
}

ALL = (MACHINE_SPECS, MACHINE_CAN, MACHINE_REFRESH)

__all__ = ["ALL", "MACHINE_SPECS", "MACHINE_CAN", "MACHINE_REFRESH"]
