"""Tests for select_probe_plugins: version selection over a synthetic catalog.

The selection logic runs against a synthetic PyPI catalog (no network). The
network layer is exercised through a fake urlopen, so no test touches PyPI.
"""

from __future__ import annotations

import io
import json

import pytest
import select_probe_plugins as sel


def _release(contract=None, kit=None, extras=None, yanked=False):
    requires = []
    if contract is not None:
        requires.append(f"tai42-contract{contract}")
    if kit is not None:
        requires.append(f"tai42-kit{kit}")
    if extras is not None:
        requires.extend(extras)
    return {"requires_dist": requires, "yanked": yanked}


# The langfuse roster the live probes select over: 3.0.3 needs the previous core
# majors, 3.0.4 the current ones, 3.0.5 the next ones.
CATALOG = {
    "3.0.3": _release(contract="<14,>=13.2", kit="<6,>=5.0"),
    "3.0.4": _release(contract="<15,>=14.0", kit="<7,>=6.0"),
    "3.0.5": _release(contract="<16,>=15.0", kit="<8,>=7.0"),
}


# --- core_pins ----------------------------------------------------------------


def test_core_pins_reads_contract_and_kit():
    manifest = "tai42-contract==14.0.0\ntai42-kit==6.0.1\ntai42-cli==10.0.1\n"
    assert sel.core_pins(manifest) == ("14.0.0", "6.0.1")


def test_core_pins_missing_kit_raises():
    with pytest.raises(sel.ProbeError, match="missing a required core pin: tai42-kit"):
        sel.core_pins("tai42-contract==14.0.0\n")


# --- admits -------------------------------------------------------------------


def test_admits_true_when_both_specifiers_contain_the_pins():
    assert sel.admits(["tai42-contract<15,>=14.0", "tai42-kit<7,>=6.0"], "14.0.0", "6.0.1")


def test_admits_false_when_contract_excludes_the_pin():
    assert not sel.admits(["tai42-contract<14,>=13.2", "tai42-kit<6,>=5.0"], "14.0.0", "6.0.1")


def test_admits_true_when_no_core_requirement_declared():
    assert sel.admits(["requests>=2"], "14.0.0", "6.0.1")


def test_admits_ignores_extra_gated_core_requirement():
    # A core requirement behind an unrequested extra is not applied by a plain
    # install, so it does not make the release incompatible.
    assert sel.admits(
        ['tai42-contract<14,>=13.2; extra == "legacy"'], "14.0.0", "6.0.1"
    )


# --- select_probe_versions ----------------------------------------------------


def test_selects_current_core_accept_and_previous_core_refuse():
    # Pinned to contract 14 / kit 6: accept the newest admitting (3.0.4) and
    # refuse the newest OLDER excluding release (3.0.3), never the newer 3.0.5
    # that pins to a future core.
    assert sel.select_probe_versions(CATALOG, "14.0.0", "6.0.1", "p") == ("3.0.4", "3.0.3")


def test_selects_next_core_accept_and_current_core_refuse():
    # Pinned to contract 15 / kit 7: accept 3.0.5, refuse the newest older
    # excluding release (3.0.4).
    assert sel.select_probe_versions(CATALOG, "15.0.0", "7.0.1", "p") == ("3.0.5", "3.0.4")


def test_yanked_admitting_release_is_skipped():
    catalog = {
        "3.0.3": _release(contract="<14,>=13.2", kit="<6,>=5.0"),
        "3.0.4": _release(contract="<15,>=14.0", kit="<7,>=6.0", yanked=True),
        "3.0.4post1": _release(contract="<15,>=14.0", kit="<7,>=6.0"),
    }
    assert sel.select_probe_versions(catalog, "14.0.0", "6.0.1", "p") == (
        "3.0.4post1",
        "3.0.3",
    )


def test_no_admitting_release_raises():
    with pytest.raises(sel.ProbeError, match="no compatible plugin is published yet"):
        sel.select_probe_versions(CATALOG, "99.0.0", "99.0.0", "p")


def test_no_older_refusing_release_raises():
    # Only one release, and it admits: nothing older to refuse.
    catalog = {"3.0.4": _release(contract="<15,>=14.0", kit="<7,>=6.0")}
    with pytest.raises(sel.ProbeError, match="no previous-generation plugin to refuse"):
        sel.select_probe_versions(catalog, "14.0.0", "6.0.1", "p")


def test_non_pep440_version_is_not_a_candidate():
    catalog = {
        "not-a-version": _release(contract="<15,>=14.0", kit="<7,>=6.0"),
        "3.0.4": _release(contract="<15,>=14.0", kit="<7,>=6.0"),
        "3.0.3": _release(contract="<14,>=13.2", kit="<6,>=5.0"),
    }
    assert sel.select_probe_versions(catalog, "14.0.0", "6.0.1", "p") == ("3.0.4", "3.0.3")


def test_null_requires_dist_release_is_skipped():
    # A newer release with unknown (null) metadata is not trusted as compatible;
    # the next admitting release with real metadata is selected instead.
    catalog = {
        "3.0.3": _release(contract="<14,>=13.2", kit="<6,>=5.0"),
        "3.0.4": _release(contract="<15,>=14.0", kit="<7,>=6.0"),
        "3.0.5": {"requires_dist": None, "yanked": False},
    }
    assert sel.select_probe_versions(catalog, "14.0.0", "6.0.1", "p") == ("3.0.4", "3.0.3")


def test_all_null_requires_dist_raises_no_accept():
    catalog = {
        "3.0.4": {"requires_dist": None, "yanked": False},
        "3.0.5": {"requires_dist": None, "yanked": False},
    }
    with pytest.raises(sel.ProbeError, match="no compatible plugin is published yet"):
        sel.select_probe_versions(catalog, "14.0.0", "6.0.1", "p")


def test_prerelease_is_not_a_candidate():
    # A newer admitting pre-release is not what a plain install resolves to, so
    # the newest admitting stable release is selected instead.
    catalog = {
        "3.0.3": _release(contract="<14,>=13.2", kit="<6,>=5.0"),
        "3.0.4": _release(contract="<15,>=14.0", kit="<7,>=6.0"),
        "3.1.0rc1": _release(contract="<15,>=14.0", kit="<7,>=6.0"),
    }
    assert sel.select_probe_versions(catalog, "14.0.0", "6.0.1", "p") == ("3.0.4", "3.0.3")


# --- fetch_catalog (fake urlopen, no network) --------------------------------


def _fake_opener(payloads):
    def opener(url, timeout=None):
        if url not in payloads:
            raise AssertionError(f"unexpected url: {url}")
        return io.BytesIO(json.dumps(payloads[url]).encode())

    return opener


def test_fetch_catalog_reads_per_release_metadata():
    base = "https://pypi.org/pypi/plug"
    payloads = {
        f"{base}/json": {"releases": {"1.0.0": [], "1.1.0": []}},
        f"{base}/1.0.0/json": {
            "info": {"requires_dist": ["tai42-contract<14,>=13.2"], "yanked": False}
        },
        f"{base}/1.1.0/json": {
            "info": {"requires_dist": ["tai42-contract<15,>=14.0"], "yanked": True}
        },
    }
    catalog = sel.fetch_catalog("plug", opener=_fake_opener(payloads))
    assert catalog == {
        "1.0.0": {"requires_dist": ["tai42-contract<14,>=13.2"], "yanked": False},
        "1.1.0": {"requires_dist": ["tai42-contract<15,>=14.0"], "yanked": True},
    }


def test_fetch_catalog_no_releases_raises():
    base = "https://pypi.org/pypi/plug"
    opener = _fake_opener({f"{base}/json": {"releases": {}}})
    with pytest.raises(sel.ProbeError, match="no releases on PyPI"):
        sel.fetch_catalog("plug", opener=opener)


# --- CLI ----------------------------------------------------------------------


def test_main_prints_github_output_lines(tmp_path, capsys, monkeypatch):
    manifest = tmp_path / "req.txt"
    manifest.write_text("tai42-contract==14.0.0\ntai42-kit==6.0.1\n")
    monkeypatch.setattr(sel, "fetch_catalog", lambda plugin: CATALOG)
    assert sel.main(["--manifest", str(manifest)]) == 0
    assert capsys.readouterr().out == "accept=3.0.4\nrefuse=3.0.3\n"


def test_main_missing_manifest_exit_1(tmp_path, capsys):
    assert sel.main(["--manifest", str(tmp_path / "absent.txt")]) == 1
    assert "error:" in capsys.readouterr().err


def test_main_no_compatible_release_exit_1(tmp_path, capsys, monkeypatch):
    manifest = tmp_path / "req.txt"
    manifest.write_text("tai42-contract==99.0.0\ntai42-kit==99.0.0\n")
    monkeypatch.setattr(sel, "fetch_catalog", lambda plugin: CATALOG)
    assert sel.main(["--manifest", str(manifest)]) == 1
    assert "no compatible plugin is published yet" in capsys.readouterr().err
