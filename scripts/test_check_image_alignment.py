"""Tests for check_image_alignment: synthetic fixtures only, no network.

The git path runs against a local repo created in tmp_path carrying a synthetic
marketplace-registry.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_image_alignment as gate
import pytest

RANGE_FILE = "e2e/docs-demo/marketplace-registry.py"


def _registry_module(contract: str | None, *, spec: bool = True) -> str:
    """A minimal stdlib-only marketplace-registry.py carrying `_SPEC['contract']`."""
    if not spec:
        return "OTHER = {}\n"
    if contract is None:
        return "_SPEC = {'name': 'alerts-relay'}\n"
    return f"_SPEC = {{'name': 'alerts-relay', 'contract': {contract!r}}}\n"


# --- pin parsing --------------------------------------------------------------


def test_contract_pin_found():
    text = "tai42-kit==5.0.1\ntai42-contract==13.0.0\n"
    assert gate.contract_pin(text, "tai42-contract") == "13.0.0"


def test_contract_pin_with_extras():
    text = "tai42-contract[toolbox,files]==13.0.0\n"
    assert gate.contract_pin(text, "tai42-contract") == "13.0.0"


def test_contract_pin_ignores_comments_and_blanks():
    text = "# header\n\ntai42-contract==7.2.0  # trailing note\n"
    assert gate.contract_pin(text, "tai42-contract") == "7.2.0"


def test_contract_pin_missing_raises():
    with pytest.raises(gate.AlignmentError, match="no tai42-contract pin"):
        gate.contract_pin("tai42-kit==5.0.1\n", "tai42-contract")


def test_contract_pin_duplicate_raises():
    text = "tai42-contract==13.0.0\ntai42-contract==12.0.0\n"
    with pytest.raises(gate.AlignmentError, match="pinned 2 times"):
        gate.contract_pin(text, "tai42-contract")


# --- declared range parsing ---------------------------------------------------


def test_declared_range_present():
    assert gate.declared_range(_registry_module(">=13.0,<14")) == ">=13.0,<14"


def test_declared_range_missing_spec_raises():
    with pytest.raises(gate.AlignmentError, match=r"no _SPEC\['contract'\]"):
        gate.declared_range(_registry_module(None, spec=False))


def test_declared_range_missing_contract_raises():
    with pytest.raises(gate.AlignmentError, match=r"no _SPEC\['contract'\]"):
        gate.declared_range(_registry_module(None))


def test_declared_range_load_failure_raises():
    with pytest.raises(gate.AlignmentError, match="failed to load"):
        gate.declared_range("this is not valid python =\n")


# --- membership ---------------------------------------------------------------


def test_verdict_inside():
    assert gate.verdict("13.0.0", ">=13.0,<14").aligned is True


def test_verdict_below():
    assert gate.verdict("11.0.0", ">=13.0,<14").aligned is False


def test_verdict_above():
    assert gate.verdict("14.0.0", ">=13.0,<14").aligned is False


def test_verdict_range_spanning_two_majors():
    assert gate.verdict("12.5.0", ">=12.0,<14").aligned is True
    assert gate.verdict("13.0.0", ">=12.0,<14").aligned is True
    assert gate.verdict("14.0.0", ">=12.0,<14").aligned is False


def test_verdict_prerelease_pin():
    assert gate.verdict("13.5.0rc1", ">=13.0,<14").aligned is True


def test_verdict_unparsable_range_raises():
    with pytest.raises(gate.AlignmentError, match="unparsable contract range"):
        gate.verdict("13.0.0", "not-a-range")


def test_verdict_unparsable_version_raises():
    with pytest.raises(gate.AlignmentError, match="unparsable pinned version"):
        gate.verdict("not-a-version", ">=13.0,<14")


# --- local git path -----------------------------------------------------------


def _make_studio_repo(root: Path, contract: str) -> str:
    """Init a local repo with a marketplace-registry.py; return the commit sha."""
    root.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    # Allow read_studio_range_file's fetch-by-sha over the local transport.
    git("config", "uploadpack.allowAnySHA1InWant", "true")
    git("config", "uploadpack.allowReachableSHA1InWant", "true")
    file_path = root / RANGE_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(_registry_module(contract))
    git("add", "-A")
    git("commit", "-q", "-m", "registry")
    return git("rev-parse", "HEAD").strip()


def test_read_studio_range_file_local(tmp_path):
    repo = tmp_path / "studio"
    sha = _make_studio_repo(repo, ">=13.0,<14")
    text = gate.read_studio_range_file(str(repo), sha, RANGE_FILE)
    assert gate.declared_range(text) == ">=13.0,<14"


def test_read_studio_range_file_bad_sha_raises(tmp_path):
    repo = tmp_path / "studio"
    _make_studio_repo(repo, ">=13.0,<14")
    with pytest.raises(gate.AlignmentError, match="git fetch"):
        gate.read_studio_range_file(str(repo), "0" * 40, RANGE_FILE)


def test_read_studio_range_file_missing_path_raises(tmp_path):
    repo = tmp_path / "studio"
    sha = _make_studio_repo(repo, ">=13.0,<14")
    with pytest.raises(gate.AlignmentError, match="git show"):
        gate.read_studio_range_file(str(repo), sha, "e2e/docs-demo/absent.py")


# --- CLI end to end -----------------------------------------------------------


def _write_inputs(tmp_path: Path, pin_version: str, contract_range: str):
    repo = tmp_path / "studio"
    sha = _make_studio_repo(repo, contract_range)
    pins = tmp_path / "pins.txt"
    pins.write_text(f"tai42-kit==5.0.1\ntai42-contract=={pin_version}\n")
    ref = tmp_path / "STUDIO_REF"
    ref.write_text(sha + "\n")
    return repo, pins, ref, sha


def _argv(repo, pins, ref):
    return [
        "--pins",
        str(pins),
        "--studio-ref-file",
        str(ref),
        "--studio-repo",
        str(repo),
    ]


def test_main_inside_exit_0(tmp_path, capsys):
    repo, pins, ref, _ = _write_inputs(tmp_path, "13.0.0", ">=13.0,<14")
    assert gate.main(_argv(repo, pins, ref)) == 0
    out = capsys.readouterr().out
    assert out.startswith("aligned:")
    assert "13.0.0" in out and ">=13.0,<14" in out


def test_main_outside_exit_1(tmp_path, capsys):
    repo, pins, ref, _ = _write_inputs(tmp_path, "13.0.0", ">=11.0,<12")
    assert gate.main(_argv(repo, pins, ref)) == 1
    out = capsys.readouterr().out
    assert "::error::platform and studio not aligned" in out
    assert "13.0.0" in out and ">=11.0,<12" in out


def test_main_bad_sha_exit_2(tmp_path, capsys):
    repo, pins, _, _ = _write_inputs(tmp_path, "13.0.0", ">=13.0,<14")
    ref = tmp_path / "bad_ref"
    ref.write_text("not-a-sha\n")
    assert gate.main(_argv(repo, pins, ref)) == 2
    assert "::error::" in capsys.readouterr().out


def test_main_missing_pin_exit_2(tmp_path, capsys):
    repo, _, ref, _ = _write_inputs(tmp_path, "13.0.0", ">=13.0,<14")
    pins = tmp_path / "empty_pins.txt"
    pins.write_text("tai42-kit==5.0.1\n")
    assert gate.main(_argv(repo, pins, ref)) == 2
    assert "::error::no tai42-contract pin" in capsys.readouterr().out


def test_main_missing_studio_repo_exit_2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("STUDIO_REPO_URL", raising=False)
    _, pins, ref, _ = _write_inputs(tmp_path, "13.0.0", ">=13.0,<14")
    argv = ["--pins", str(pins), "--studio-ref-file", str(ref)]
    assert gate.main(argv) == 2
    assert "::error::--studio-repo is required" in capsys.readouterr().out
