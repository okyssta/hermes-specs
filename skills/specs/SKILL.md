---
name: specs
description: "Quote this machine's measured capabilities instead of guessing its hardware."
version: 1.0.0
author: kyssta
license: MIT
metadata:
  hermes:
    tags: [hardware, capability, feasibility, system-prompt, gpu, docker]
---

# Measured machine facts

Every new session starts with a capability card from `specs`: OS/arch, CPU, RAM,
GPU, free disk, installed tools and init system, with the date it was measured.
Those values are measurements. Treat them as stronger evidence than any habit
about "a typical machine", and quote them when a plan depends on them.

## Which surface to reach for

| Question | Use |
| --- | --- |
| "What does this machine have?" | `machine_specs(section="all")` |
| "How much RAM / which GPU / how much disk?" | `machine_specs(section="memory"|"gpu"|"disk")` |
| "Is `ffmpeg` installed? which version?" | `machine_specs(section="tools")` |
| "Can this machine run a 7B model at 4-bit?" | `machine_can(task="run a 7B model at 4-bit")` |
| "Can I run Docker here?" | `machine_can(task="run Docker containers")` |
| "I just installed something" | `machine_refresh(section="tools")`, then re-check |
| The user asks for the raw detail | `machine_specs`; the card is a summary |

Use `machine_can` for a *decision*, `machine_specs` for a *fact*. If
`machine_can` returns `uncertain`, read the relevant `machine_specs` section and
decide from the numbers — do not silently round "uncertain" up to "yes".

## How to talk about the results

- Quote the number and its provenance: "11.6 GiB total, 7.0 GiB available
  (`/proc/meminfo`)" beats "plenty of RAM".
- `machine_can` is a heuristic and says so. When you pass its verdict on, keep
  the label: "specs rates this supported, but CPU-only inference at roughly 3
  tokens/s means a long answer will take minutes."
- Never invent hardware. If a section says `unknown`, say the probe did not
  answer and either run `machine_refresh(section=...)` or ask.
- The card is measured once and cached (24 h by default). If the user says they
  just installed a GPU driver, a tool or more RAM, refresh before disagreeing
  with them.

## Planning rules that follow from the measurements

- A GPU line with `compute=False` or `vendor=virtual` means **no CUDA/ROCm**:
  never write a plan whose first step is `pip install torch --index-url ...cu124`.
- `docker` present but daemon `unreachable` means the CLI exists and containers
  still cannot run; say which of the two requirements is missing.
- Check free disk before any download, model pull, dataset or video render, and
  put the number in the plan ("~4 GB of weights, 85 GiB free").
- On a small core count (1-2), prefer CPU-cheap approaches and say that encodes
  or inference will be slow rather than implying they will be quick.
- `system: not containerized` plus `elevated` explains whether `apt`, `systemctl`
  or unit installation is even meaningful here.

## Failure modes to avoid

- Do not ask the user "how much RAM do you have?" when the card already answers
  it; ask only about things the probes cannot see (budget, licence, intent).
- Do not treat the card as a benchmark. It says what exists, not how fast it is;
  `machine_can` gives rough estimates and labels them as such.
- Do not paste the whole fact sheet when one line answers the question.
