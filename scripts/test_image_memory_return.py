"""Tests for image_memory_return's decisions: synthetic /proc texts and readings only.

The burst itself needs the kit and runs inside the image; these cover the
functions that decide its verdict.
"""

from __future__ import annotations

import image_memory_return as probe
import pytest

MAPS_JEMALLOC = """\
55d0c0a00000-55d0c0a01000 r--p 00000000 08:01 1234  /usr/local/bin/python3.13
7f1a2b000000-7f1a2b021000 rw-p 00000000 00:00 0
7f1a2c000000-7f1a2c00a000 r--p 00000000 08:01 5678  /usr/lib/libjemalloc.so.2
7f1a2c00a000-7f1a2c0a0000 r-xp 0000a000 08:01 5678  /usr/lib/libjemalloc.so.2
7f1a2d000000-7f1a2d028000 r--p 00000000 08:01 9012  /usr/lib/libc.so.6
7ffd1e000000-7ffd1e021000 rw-p 00000000 00:00 0  [stack]
"""

MAPS_GLIBC = """\
55d0c0a00000-55d0c0a01000 r--p 00000000 08:01 1234  /usr/local/bin/python3.13
7f1a2b000000-7f1a2b021000 rw-p 00000000 00:00 0
7f1a2d000000-7f1a2d028000 r--p 00000000 08:01 9012  /usr/lib/libc.so.6
7ffd1e000000-7ffd1e021000 rw-p 00000000 00:00 0  [stack]
"""

STATUS = """\
Name:\tpython
VmPeak:\t 1234567 kB
VmHWM:\t  229376 kB
VmRSS:\t   92160 kB
Threads:\t25
"""

# --- maps ---------------------------------------------------------------------


def test_maps_names_the_preloaded_allocator():
    assert probe.maps_library(MAPS_JEMALLOC, probe.ALLOCATOR_LIBRARY)


def test_maps_without_the_allocator():
    assert not probe.maps_library(MAPS_GLIBC, probe.ALLOCATOR_LIBRARY)


def test_maps_matches_the_file_name_not_a_directory():
    text = (
        "7f1a2c000000-7f1a2c00a000 r--p 00000000 08:01 5678"
        "  /opt/libjemalloc/lib/libc.so.6\n"
    )
    assert not probe.maps_library(text, probe.ALLOCATOR_LIBRARY)


def test_maps_ignores_anonymous_mappings():
    assert not probe.maps_library(
        "7f1a2b000000-7f1a2b021000 rw-p 00000000 00:00 0\n", ""
    )


# --- interpreter allocator ----------------------------------------------------


def test_interpreter_uses_malloc():
    environ = {"PYTHONMALLOC": "malloc"}
    assert probe.interpreter_uses(environ, probe.INTERPRETER_ALLOCATOR)


def test_interpreter_allocator_unset():
    assert not probe.interpreter_uses({}, probe.INTERPRETER_ALLOCATOR)


@pytest.mark.parametrize("value", ["pymalloc", "mimalloc", "malloc_debug", ""])
def test_interpreter_allocator_other(value):
    environ = {"PYTHONMALLOC": value}
    assert not probe.interpreter_uses(environ, probe.INTERPRETER_ALLOCATOR)


# --- status -------------------------------------------------------------------


@pytest.mark.parametrize(("field", "kb"), [("VmRSS", 92160), ("VmHWM", 229376)])
def test_status_reads_the_field(field, kb):
    assert probe.status_kb(STATUS, field) == kb


def test_status_does_not_match_a_field_prefix():
    with pytest.raises(probe.ProbeError, match="VmRS is not"):
        probe.status_kb(STATUS, "VmRS")


def test_status_missing_field_raises():
    with pytest.raises(probe.ProbeError, match="VmSwap is not"):
        probe.status_kb(STATUS, "VmSwap")


def test_status_unexpected_unit_raises():
    with pytest.raises(probe.ProbeError, match="expected kB"):
        probe.status_kb("VmRSS:\t 90 MB\n", "VmRSS")


# --- held share and bound -----------------------------------------------------


def test_held_share_of_the_growth():
    assert probe.held_share(base_kb=60, peak_kb=260, idle_kb=110) == pytest.approx(0.25)


def test_held_share_idle_back_at_base_is_zero():
    assert probe.held_share(base_kb=60, peak_kb=260, idle_kb=60) == 0


def test_held_share_idle_below_base_is_zero():
    assert probe.held_share(base_kb=60, peak_kb=260, idle_kb=50) == 0


def test_held_share_nothing_returned_is_one():
    assert probe.held_share(base_kb=60, peak_kb=260, idle_kb=260) == 1


@pytest.mark.parametrize("peak_kb", [60, 40])
def test_held_share_without_growth_raises(peak_kb):
    with pytest.raises(probe.ProbeError, match="grew nothing"):
        probe.held_share(base_kb=60, peak_kb=peak_kb, idle_kb=60)


def test_held_share_idle_above_peak_raises():
    with pytest.raises(probe.ProbeError, match="exceeds the peak"):
        probe.held_share(base_kb=60, peak_kb=260, idle_kb=300)


def test_bound_passes_a_returned_burst():
    share = probe.held_share(base_kb=60, peak_kb=225, idle_kb=90)
    assert probe.within_bound(share, probe.HELD_SHARE_MAX)


def test_bound_fails_a_kept_burst():
    share = probe.held_share(base_kb=57, peak_kb=194, idle_kb=183)
    assert not probe.within_bound(share, probe.HELD_SHARE_MAX)


def test_bound_is_exclusive():
    assert not probe.within_bound(0.5, 0.5)
