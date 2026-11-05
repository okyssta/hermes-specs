# specs

**Hermes starts every session knowing the real machine — not an imaginary one.**

`specs` measures this computer once, caches a compact capability card, and freezes
that card into the system prompt of every new session. Three tools carry the detail:
`machine_specs` (the fact sheet, with how each value was obtained), `machine_can`
(a labelled feasibility heuristic with its arithmetic shown) and `machine_refresh`
(re-probe after the machine changes).

## Why

An agent's picture of "the machine" comes from priors: laptops have GPUs, Docker is
installed, `brew` exists, RAM is plentiful. Every plan built on those priors is a
coin flip, and the user is the one who finds out:

- "Install `torch` with CUDA" on a VM with a virtio display adapter.
- "Run `docker compose up`" where the CLI is missing (or the daemon is not running).
- "Pull the 70B model" on 11 GB of RAM.
- "Render the 4K cut" with 2 GB of disk left.

`specs` replaces the prior with a measurement, injected before the model plans.
It is deliberately small: a 1200-character card, six sections of facts, three tools,
one JSON cache. No account, no network, no daemon, no telemetry.

## What it adds

| Surface | Kind | What it does |
| --- | --- | --- |
| `specs.card` | system-prompt section | ~500-char capability card (`after_memory`, capped at 1200 by default), re-rendered at most once a minute per process |
| `machine_specs` | tool | Fact sheet for `all`/`system`/`cpu`/`memory`/`gpu`/`disk`/`tools`; every line names its provenance |
| `machine_can` | tool | Heuristic verdict — `supported` / `not supported` / `uncertain` — for local LLMs, Docker, CUDA, headless browsers, ffmpeg video, systemd |
| `machine_refresh` | tool | Re-probe one section or everything, rewrite the cache, return the fresh sheet |
| `/specs` | command | `card`, `status`, `refresh [section]`, `can <task>`, `<section>`, `help` |
| `specs` | bundled skill | When to quote a measurement instead of guessing; how to pass a heuristic verdict on honestly |

### Tools

```
machine_specs(section="all")      # or system | cpu | memory | gpu | disk | tools
machine_can(task="run a 7B parameter model at 4-bit")
machine_refresh(section="tools")  # or all
```

`machine_specs` output looks like this (real, from the demo host):

```
[Memory] status: measured · probed 2026-11-05 14:27 UTC
  total RAM: 11.6 GiB — obtained by: /proc/meminfo MemTotal; /proc/meminfo MemAvailable
  available RAM: 7.0 GiB — obtained by: MemAvailable / GlobalMemoryStatusEx / vm_stat
```

## Quickstart

```bash
# from the catalog, once published
hermes plugins install okyssta/hermes-specs --enable

# or from a checkout (a local path is not an install argument)
git clone https://github.com/okyssta/hermes-specs "$HERMES_HOME/plugins/specs"
hermes plugins enable specs
hermes plugins list                       # specs → enabled
```

Start a new session: the card is already in the prompt. Then:

```
/specs                     # the card
/specs gpu                 # one section, with provenance
/specs can run a 7B model at 4-bit
/specs refresh tools       # after installing something
```

Nothing needs configuring. The defaults:

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Inject the card; tools stay registered either way |
| `ttl_hours` | `24` | Reuse a measurement this long before re-probing |
| `card_max_chars` | `1200` | Hard cap for the injected card (core drops an oversized section) |
| `probe_timeout_ms` | `1500` | Per-subprocess timeout; a timeout becomes `unknown` |
| `gpu_probe` | `true` | Look for nvidia-smi / rocm-smi / lspci / system_profiler / Win32_VideoController |
| `docker_probe` | `true` | Run `docker info` to see whether a daemon answers (read-only) |
| `tools` | 19 names | Tools to look up on PATH and version |

## The card

What this host's sessions actually receive (513 of 1200 characters):

```
specs · machine capability card
os: Linux · 6.17.0-1019-oracle · aarch64
cpu: 2 logical cores · Neoverse-N1
ram: 7.0 GiB available of 11.6 GiB
gpu: virtual display only (no CUDA/ROCm)
disk: 84.6 GiB free of 192.7 GiB
tools: python3 3.14.9 · pip 26.2.2 · uv 0.13.5 · node 26.7.0 · npm 11.19.0 · git 2.43.0 · ffmpeg 9.0.1-11 · curl 8.5.0 · tesseract 5.3.4 · systemctl 255.4-1ubuntu8.18 · apt 2.8.3 · no docker
system: systemd (pid 1) · not containerized · elevated
measured 2026-11-05; call machine_specs for detail
```

Two design constraints shape it:

- **The cap is a correctness property.** Core *drops* a section that exceeds
  `max_chars` instead of truncating it, so an over-long card would vanish silently.
  `render_card` therefore degrades in a fixed order — drop tool entries (`+N more`),
  drop optional lines, finally hard-truncate the body — and keeps the header and
  footer intact. Tests assert `len(card) <= cap` for caps from 1200 down to 40.
- **It carries facts, never identifiers.** Built from whitelisted fields and then
  scrubbed for paths, IPv4, MAC addresses and e-mail addresses.

## Feasibility, and how honest it is

`machine_can` classifies the task, compares it with the measured facts, and prints
the arithmetic — including the assumptions — so a wrong verdict is arguable.

| Task | Requirements checked | Verdict on this host |
| --- | --- | --- |
| 7B model at 4-bit | weights `7e9 × 0.55 B` = 3.85 GB; overhead `25% + 0.5 GB` = 1.50 GB; total 5.35 GB vs 90% of available RAM (or 90% of VRAM) | **supported**, with a CPU-inference caveat (~2.8 tok/s) |
| Docker containers | a `docker` binary **and** a daemon that answers `docker info` | **not supported** (no binary) |
| 4K video with ffmpeg | ffmpeg present; `40 Mbit/s × duration × 1.2 + 0.5 GB` vs free disk | **supported**, with a software-encode caveat (~30 min for 60 s on 2 cores) |

Thresholds: `need ≤ 0.95 × capacity` → supported; up to `1.2 ×` → uncertain; beyond
→ not supported. Capacity is 90% of available RAM on CPU, 90% of VRAM on a compute
GPU, 65% of available RAM on Apple silicon. Precision is read from the task
(`q4_K_M`, `4-bit`, `int8`, `fp16`…); when the task says "quantized" but not which,
4-bit is assumed **and the assumption is printed**. When no parameter count is given,
the verdict is `uncertain` and asks for one rather than guessing.

Every verdict says `heuristic verdict, not a guarantee`. Docker with a binary but no
daemon is `not supported` (with "a daemon would have to be started"); an unreadable
socket is `uncertain`, never rounded up.

## How measurements are made

| Section | Probe | Fallbacks |
| --- | --- | --- |
| system | `platform.system/release/machine`, `platform.python_version()`, `/proc/1/comm`, `/proc/1/cgroup`, `/.dockerenv`, `/run/.containerenv`, `os.geteuid()`, `shutil.which` over bash/zsh/fish/sh/pwsh | launchd on macOS, services.exe on Windows |
| cpu | `os.cpu_count()`, `model name` in `/proc/cpuinfo`, ARM `CPU part` map | `lscpu` "Model name"; `platform.processor()` |
| memory | `/proc/meminfo` `MemTotal`/`MemAvailable` | `MemFree+Buffers+Cached` (flagged as an estimate); macOS `sysctl hw.memsize` + `vm_stat`; Windows `GlobalMemoryStatusEx` via ctypes |
| gpu | `nvidia-smi --query-gpu=name,memory.total`, `rocm-smi --showproductname` | `system_profiler SPDisplaysDataType` (macOS), `lspci` display lines (classified nvidia/amd/virtual/none), PowerShell `Win32_VideoController` |
| disk | `shutil.disk_usage` on the volume holding the Hermes home | walks up to the nearest existing parent; the path is never printed |
| tools | `shutil.which` for presence, then `<tool> --version`/`-V`; only the version token is kept (banners embed install paths) | absent tools are recorded as absent, not as errors |

Each subprocess gets a hard timeout (`probe_timeout_ms`, default 1.5 s; the docker
daemon check gets 2.5 s). A probe that raises, times out or cannot parse becomes
`status: "unknown"` for that section and nothing else. No shell is involved — argv
lists only — and the docker check is `docker info`, which never starts a daemon.

## Cache

- Path: `<hermes home>/plugin-data/specs/specs.json` (resolved through
  `hermes_constants.get_hermes_home`, with a `HERMES_HOME` fallback).
- Per-section timestamps: a fresh section is reused, a stale one is re-probed alone.
- TTL default 24 h (`ttl_hours`); `machine_refresh` forces a section immediately.
- Corrupt JSON, a wrong schema version or a damaged section is treated as absent:
  the plugin re-probes and rewrites. Writes are atomic (`tmp` + `os.replace`); a
  read-only disk makes `save_cache` return `False` rather than raise.
- Writes never happen inside `register()`, so startup stays cheap and the admission
  probe (30 s budget) cannot be blocked by a measurement.

## Privacy

The card is copied into every session's prompt, so it is capability-only by
construction. It never contains: username, hostname, home path, serial numbers, MAC
addresses, IP addresses, environment-variable values, file contents, process lists
or command history. The disk line says "hermes data volume", not a mount path; the
privilege line says "elevated", not an account name. A final scrub pass replaces
anything path- or address-shaped with `(path)` / `(redacted)`. Tests assert that
the real card on the test host contains none of the username, hostname, home path,
MAC or IPv4, and that a deliberately poisoned fact set is scrubbed.

## Limits and unverified claims

Stated plainly, because a capability tool that overclaims is worse than none:

- **Only Linux was executed here.** The macOS (`sysctl`, `vm_stat`,
  `system_profiler`, Apple silicon) and Windows (`GlobalMemoryStatusEx`,
  `Win32_VideoController`) paths are written and unit-mocked, but never run on real
  hardware in this build.
- **No real GPU was exercised.** The demo host has a virtio display adapter only, so
  the nvidia-smi / rocm-smi / Apple paths are covered by mocked tests, not by
  hardware.
- **No Docker daemon was exercised.** This host has no docker binary; the
  reachable-daemon and permission-denied branches are unit-tested with recorded
  output shapes.
- **The numbers are planning estimates, not benchmarks.** Token/s
  (`cores × 1.4 ÷ (params/7)`), video bitrates (8/16/40 Mbit/s), the 25% model
  overhead and the 1.2× muxing headroom are rules of thumb. They are printed so they
  can be argued with.
- **Version parsing takes the first numeric token** of a banner. A tool with an
  unusual banner may report no version rather than a wrong one.
- **The ARM `CPU part` table is partial**; unknown parts fall back to
  `ARMv8 (part 0x…)`, and `lscpu` is preferred when present.
- **Disk is one volume** — the filesystem holding the Hermes home — not every mount.
- A cold measurement costs ~0.4 s here, mostly `docker info` when Docker is present;
  the tools loop has a total budget (4× the per-probe timeout, 2-10 s) so a hung
  binary cannot stall a session start. The card renderer memoises for 60 s per
  process and the cache makes later calls sub-millisecond.
- `hermes plugins validate` reports **caution** from its security scanner for
  `proc_access` (the `/proc/cpuinfo`, `/proc/meminfo`, `/proc/1/comm` reads). That
  is the measurement itself: read-only, no writes, no credentials.

## Development

```bash
# tests (162 cases: probes, card caps, cache TTL/corruption, arithmetic, handlers,
# registration through both a recording context and the real PluginManager — that
# last one skips where Hermes' own deps (ruamel) are absent, e.g. the reference venv)
PYTHONPATH=/root/candidates/specs:/root/.hermes/hermes-agent \
  /root/peripheral/.venv/bin/python -m pytest -q

# catalog-admission gate
hermes plugins validate /root/candidates/specs

# end-to-end, in a throwaway HERMES_HOME, no network
python scripts/demo.py
python scripts/demo.py .demo-home   # run twice: the second starts from the cache
```

`tests/conftest.py` builds every fact set explicitly, so rendering and arithmetic
tests do not depend on the host running them; the probe tests mock the two
primitives (`_read_text`, `_run`) and therefore describe Linux, macOS and Windows
machines deterministically.

## Real demo output

From `python scripts/demo.py` on this host (aarch64 Oracle VM, 2 vCPU, 11.6 GiB RAM,
virtio display adapter, no Docker). Paths shortened to `<temp>`; everything else is
verbatim.

```
specs 1.0.0 demo · python 3.14.7
throwaway HERMES_HOME: <temp>
cache file: <temp>/plugin-data/specs/specs.json
cache before this run: absent or stale
tools registered: machine_specs, machine_can, machine_refresh

── 1. measure (cold or from cache) ─────────────────────────────────────────
measure() took 473.08 ms → system=measured, cpu=measured, memory=measured, gpu=measured, disk=measured, tools=measured
cache written: True (5115 bytes)

── 2. the capability card (this is what a new session sees) ────────────────
specs · machine capability card
os: Linux · 6.17.0-1019-oracle · aarch64
cpu: 2 logical cores · Neoverse-N1
ram: 7.0 GiB available of 11.6 GiB
gpu: virtual display only (no CUDA/ROCm)
disk: 84.6 GiB free of 192.7 GiB
tools: python3 3.14.9 · pip 26.2.2 · uv 0.13.5 · node 26.7.0 · npm 11.19.0 · git 2.43.0 · ffmpeg 9.0.1-11 · curl 8.5.0 · tesseract 5.3.4 · systemctl 255.4-1ubuntu8.18 · apt 2.8.3 · no docker
system: systemd (pid 1) · not containerized · elevated
measured 2026-11-05; call machine_specs for detail

[513/1200 chars]

── 3. machine_specs(section="tools") — measured values and how ─────────────
machine specs · tools · measured 0s ago · cache TTL 24h · every value names how it was obtained

[Tools] status: measured · probed 2026-11-05 14:34 UTC
  apt: version 2.8.3 — obtained by: apt --version
  brew: absent — obtained by: shutil.which('brew') found nothing
  curl: version 8.5.0 — obtained by: curl --version
  dnf: absent — obtained by: shutil.which('dnf') found nothing
  docker: absent — obtained by: shutil.which('docker') found nothing
  ffmpeg: version 9.0.1-11 — obtained by: ffmpeg -version
  git: version 2.43.0 — obtained by: git --version
  jq: absent — obtained by: shutil.which('jq') found nothing
  node: version 26.7.0 — obtained by: node --version

── 4. machine_can — three plans checked against the measurements ───────────

machine_can("run a 7B parameter model at 4-bit") — heuristic verdict, not a guarantee — the arithmetic is shown so you can check it
verdict: supported
why: the model fits: 5.35 GB needed against 6.75 GB usable.
matched: model

arithmetic:
  - 7B params × 0.550 bytes/param (4-bit) = 3.85 GB of weights
  - runtime overhead (KV cache, context, allocator) ≈ 25% of weights + 0.5 GB = 1.50 GB
  - total need ≈ 5.35 GB
  - capacity: available system RAM (90% of MemAvailable), CPU inference = 6.75 GB usable (6.3 GiB); need is 79% of it

caveats and assumptions:
  - no compute GPU with VRAM: this runs on 2 CPU core(s) at roughly 2.8 tokens/s (rough estimate); a 500-token answer would take about 3 min
  - quality drops at 4-bit; expect a measurable loss on reasoning tasks

measured facts used:
  - ram: 7.0 GiB available of 11.6 GiB — /proc/meminfo MemTotal; /proc/meminfo MemAvailable
  - gpu: virtual (Red Hat, Inc. Virtio 1.0 GPU), compute=False — lspci (VGA/3D/Display controller lines)
  - cpu: 2 logical core(s) Neoverse-N1 — os.cpu_count(); /proc/cpuinfo

measured 2026-11-05; re-check with machine_specs(section="...") or machine_refresh(). Verdicts describe this machine as measured, not what a different setup could do.


machine_can("run Docker containers") — heuristic verdict, not a guarantee — the arithmetic is shown so you can check it
verdict: not supported
why: there is no docker binary on PATH, so no container can be run until it is installed.
matched: docker

arithmetic:
  - requirement 1: a docker binary on PATH → absent
  - requirement 2: a reachable daemon (`docker info` answers) → not testable without the binary

measured facts used:
  - tools present: apt 2.8.3, curl 8.5.0, ffmpeg 9.0.1-11, git 2.43.0, node 26.7.0, npm 11.19.0, pip 26.2.2, python3 3.14.9, +3 more — shutil.which() + `<tool> --version`

measured 2026-11-05; re-check with machine_specs(section="...") or machine_refresh(). Verdicts describe this machine as measured, not what a different setup could do.


machine_can("render 4K video with ffmpeg") — heuristic verdict, not a guarantee — the arithmetic is shown so you can check it
verdict: supported
why: ffmpeg is installed and 84.6 GiB free easily covers ~0.86 GB.
matched: video

arithmetic:
  - requirement 1: an ffmpeg binary → present (9.0.1-11)
  - estimate: 4K (2160p) at ~40 Mbit/s for 60s ≈ 0.86 GB (1.2× muxing headroom + 0.5 GB working space)
  - free disk: 84.6 GiB

caveats and assumptions:
  - software encode on 2 CPU core(s): roughly 1 frame(s)/s at 4K, so a 60s clip at 30fps takes about 30 min (rough estimate; hardware encoders were not probed)
  - the bitrate is a planning estimate, not a measurement of ffmpeg's output

measured facts used:
  - tools present: apt 2.8.3, curl 8.5.0, ffmpeg 9.0.1-11, git 2.43.0, node 26.7.0, npm 11.19.0, pip 26.2.2, python3 3.14.9, +3 more — shutil.which() + `<tool> --version`
  - disk: 84.6 GiB free of 192.7 GiB — shutil.disk_usage() on the volume holding the Hermes home
  - cpu: 2 logical core(s) Neoverse-N1 — os.cpu_count(); /proc/cpuinfo
  - gpu: virtual (Red Hat, Inc. Virtio 1.0 GPU), compute=False — lspci (VGA/3D/Display controller lines)

measured 2026-11-05; re-check with machine_specs(section="...") or machine_refresh(). Verdicts describe this machine as measured, not what a different setup could do.


── 5. second measure in the same process (cache path) ──────────────────────
measure() took 0.80 ms · identical card: True
cold 473.08 ms vs warm 0.80 ms (cache is doing its job)

── 6. refresh one section (the manual path) ────────────────────────────────
refreshed tools in 0.47s · re-probed tools · cache updated on disk

machine specs · tools · measured 0s ago · cache TTL 24h · every value names how it was obtained

...

── result ──────────────────────────────────────────────────────────────────
PASS card within budget
PASS cache file exists
PASS a warm measure is at least 2x faster than a cold one
PASS card is stable across measures
```

A second invocation against the same home starts warm:

```
cache before this run: present, fresh
measure() took 1.97 ms → system=measured, cpu=measured, memory=measured, gpu=measured, disk=measured, tools=measured
measure() took 1.03 ms · identical card: True
```

## Disclosure

`specs` runs read-only local probes: platform/system files (`/proc/cpuinfo`,
`/proc/meminfo`, `/proc/1/comm`, `/proc/1/cgroup`, `/.dockerenv`), `shutil.disk_usage`,
short-timeout version banners for the configured tools, and `docker info` to see
whether a daemon answers — it never starts, stops or changes anything. It writes one
JSON cache under the profile's `plugin-data/specs/` and nothing else. The card and
cache hold capability facts only — no username, hostname, home path, serial number,
MAC address, IP address, environment-variable value or file contents — and are
scrubbed again before rendering. It makes no network calls, reads no credentials and
has no telemetry.

## License

MIT © 2026 kyssta
