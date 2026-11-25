#!/usr/bin/env python3
"""Prove the image's processes return the memory a burst of work freed.

The image's processes, and every process they start with the inherited
environment, run on jemalloc (`LD_PRELOAD=libjemalloc.so.2`,
`MALLOC_CONF=background_thread:true`); a host-sandbox exec runs with only its
session's environment. The dynamic loader only prints a warning
and carries on with glibc's malloc when a preload cannot be loaded, so the probe
first asserts that `libjemalloc` is mapped into its own process.

It then runs the burst a server worker sees: a pool of THREADS threads runs
ROUNDS x JOBS jq jobs, each over a freshly built JSON document of DOC_KB,
through the kit's `jq_worker` (`compile_job` / `evaluate_cached_job`), so the
real jq path allocates and frees the buffers. Nothing a job builds outlives it
and the pool's threads stay alive, idle, as a server's executor threads do.

The burst's growth is the peak (VmHWM) less the VmRSS taken just before it.
After the burst the probe idles for IDLE_WAIT_S seconds, three of jemalloc's
default 10 s dirty-decay windows, logging VmRSS every second: the background
thread purges in steps with flat stretches of several seconds between them, so
no short run of equal readings marks the end of the decay. The VmRSS at the end
of the wait is the idle reading, and the probe fails unless the share of the
growth still held there is below HELD_SHARE_MAX.

`--glibc-baseline` measures the same burst on glibc's malloc (the image run with
`LD_PRELOAD` and `MALLOC_CONF` cleared): it asserts jemalloc is NOT mapped,
prints the numbers and the verdict the bound would give, and applies no bound.

Numbers the bound is set from (VmRSS in MB; held share = (idle - base) /
(peak - base)). HELD_SHARE_MAX sits between the two allocators: well above
jemalloc's share, far below glibc's.

  host, glibc 2.39, Ubuntu libjemalloc2 5.3.0-2build1, Python 3.13.7:
    glibc       base 57  peak 194  idle 183  held share 0.92
    jemalloc    base 60  peak 225  idle  90  held share 0.18  (3 runs: 0.18-0.19)
  image, glibc 2.41, Debian libjemalloc2 5.3.0-3, Python 3.13:
    glibc       base --  peak ---  idle ---  held share ----
    jemalloc    base --  peak ---  idle ---  held share ----

The pure functions decide; `run` drives the burst, which imports the kit and so
runs where the kit is installed: the image.

Exit 0 passed, 1 failed. Every failure prints a `::error::` line naming the
cause; no silent fallback.
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ALLOCATOR_LIBRARY = "libjemalloc"

THREADS = 24
ROUNDS = 3
JOBS = 96
DOC_KB = 1500

IDLE_WAIT_S = 30
SAMPLE_INTERVAL_S = 1.0

HELD_SHARE_MAX = 0.5

PROGRAM_KEY = "image-memory-return"
PROGRAM = "[.items[] | .text | length] | add"


class ProbeError(Exception):
    """A probe failure, carrying the message the `::error::` line prints."""


# --- decisions ----------------------------------------------------------------


def maps_library(maps_text: str, library: str) -> bool:
    """Whether a `/proc/<pid>/maps` text maps a file whose name starts `library`."""
    for line in maps_text.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 6:
            continue
        name = fields[5].rsplit("/", 1)[-1]
        if name.startswith(library):
            return True
    return False


def status_kb(status_text: str, field: str) -> int:
    """The kB value of `field` (e.g. `VmRSS`) in a `/proc/<pid>/status` text."""
    prefix = f"{field}:"
    for line in status_text.splitlines():
        if line.startswith(prefix):
            value, unit = line[len(prefix) :].split()
            if unit != "kB":
                raise ProbeError(f"{field} is in {unit!r}, expected kB")
            return int(value)
    raise ProbeError(f"{field} is not in the process status")


def held_share(base_kb: int, peak_kb: int, idle_kb: int) -> float:
    """The share of the burst's growth (peak over base) still resident when idle."""
    growth = peak_kb - base_kb
    if growth <= 0:
        raise ProbeError(
            f"peak {peak_kb} kB is not above the base {base_kb} kB;"
            " the burst grew nothing to measure"
        )
    if idle_kb > peak_kb:
        raise ProbeError(f"idle {idle_kb} kB exceeds the peak {peak_kb} kB")
    return max(idle_kb - base_kb, 0) / growth


def within_bound(share: float, max_share: float) -> bool:
    """Whether the held share is below the bound."""
    return share < max_share


# --- the burst (inside the image) ---------------------------------------------


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _status(field: str) -> int:
    return status_kb(_read("/proc/self/status"), field)


def _mb(kb: int) -> int:
    return kb // 1024


def _document_text(seed: int, doc_kb: int) -> str:
    from tai42_kit.utils.data.jq_worker import envelope

    rnd = random.Random(seed)
    items = []
    size = 0
    while size < doc_kb * 1024:
        text = rnd.randbytes(rnd.randint(25, 1500)).hex()
        items.append({"text": text, "n": seed, "r": rnd.random()})
        size += len(text) + 40
    return json.dumps(envelope({"items": items}, None, 1))


def _job(seed: int, doc_kb: int) -> int:
    from tai42_kit.utils.data.jq_worker import evaluate_cached_job

    values = evaluate_cached_job(PROGRAM_KEY, _document_text(seed, doc_kb))
    if values is None:
        raise ProbeError("the jq program is not in the worker's cache")
    return len(values)


def _burst(pool: ThreadPoolExecutor) -> None:
    for round_ in range(ROUNDS):
        seeds = range(round_ * JOBS, (round_ + 1) * JOBS)
        emitted = sum(pool.map(_job, seeds, [DOC_KB] * JOBS))
        if emitted != JOBS:
            raise ProbeError(f"round {round_} emitted {emitted} values for {JOBS} jobs")
        print(f"round {round_ + 1}/{ROUNDS}: VmRSS {_mb(_status('VmRSS'))} MB")


def _idle_rss() -> int:
    readings = [_status("VmRSS")]
    deadline = time.monotonic() + IDLE_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(SAMPLE_INTERVAL_S)
        readings.append(_status("VmRSS"))
    print("idle VmRSS each second (MB):", [_mb(r) for r in readings])
    return readings[-1]


def run(glibc_baseline: bool) -> None:
    from tai42_kit.utils.data.jq_worker import JqSource, compile_job

    mapped = maps_library(_read("/proc/self/maps"), ALLOCATOR_LIBRARY)
    if glibc_baseline and mapped:
        raise ProbeError(
            f"{ALLOCATOR_LIBRARY} is mapped; the baseline must run on glibc"
        )
    if not glibc_baseline and not mapped:
        raise ProbeError(
            f"{ALLOCATOR_LIBRARY} is not mapped into the process: the image's"
            " LD_PRELOAD did not load it and the process runs on glibc's malloc"
        )
    allocator = "glibc" if glibc_baseline else "jemalloc"
    print(
        f"allocator: {allocator}; burst: {THREADS} threads,"
        f" {ROUNDS} x {JOBS} jobs of {DOC_KB} kB"
    )

    compile_job(PROGRAM_KEY, JqSource(expression=PROGRAM, prelude="", variables=()))
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        gc.collect()
        base_kb = _status("VmRSS")
        _burst(pool)
        gc.collect()
        idle_kb = _idle_rss()
        peak_kb = _status("VmHWM")

    share = held_share(base_kb, peak_kb, idle_kb)
    passed = within_bound(share, HELD_SHARE_MAX)
    print(
        f"{allocator}: base {_mb(base_kb)} MB, peak {_mb(peak_kb)} MB,"
        f" idle {_mb(idle_kb)} MB, held share {share:.2f}, bound {HELD_SHARE_MAX}"
    )
    if glibc_baseline:
        print(f"the bound would {'pass' if passed else 'fail'} this run")
        return
    if not passed:
        raise ProbeError(
            f"{share:.2f} of the burst's {_mb(peak_kb - base_kb)} MB growth is still"
            f" resident after {IDLE_WAIT_S} s idle, not below {HELD_SHARE_MAX}:"
            " the freed memory was not returned"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--glibc-baseline",
        action="store_true",
        help="measure on glibc's malloc: assert jemalloc is not mapped, apply no bound",
    )
    args = parser.parse_args(argv)
    try:
        run(args.glibc_baseline)
    except ProbeError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
