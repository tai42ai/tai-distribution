"""Tests for derive_constraints: the manifest parser and the in-venv emitter.

The manifest parser is exercised against synthetic requirements text. The venv
emitter is exercised through a fake `importlib.metadata.distributions`.
"""

from __future__ import annotations

import derive_constraints as derive
import pytest

ROSTER = """\
# First-party package set installed into the release image.
tai42-contract==14.0.0
tai42-kit==6.0.1
tai42-toolbox[http]==4.0.1
tai42-cli==10.0.1
tai42-skeleton[toolbox,files]==18.0.1
tai42-agents==7.0.1
tai42-storage-local==1.1.14
tai42-backend-arq==4.0.1
tai42-identity-redis==2.0.4
tai42-sandbox-docker==1.2.7
tai42-sandbox-local==1.1.14
"""


# --- manifest parser ----------------------------------------------------------


def test_manifest_full_roster_present_once_each():
    lines = derive.parse_manifest(ROSTER)
    assert lines == [
        "tai42-agents==7.0.1",
        "tai42-backend-arq==4.0.1",
        "tai42-cli==10.0.1",
        "tai42-contract==14.0.0",
        "tai42-identity-redis==2.0.4",
        "tai42-kit==6.0.1",
        "tai42-sandbox-docker==1.2.7",
        "tai42-sandbox-local==1.1.14",
        "tai42-skeleton==18.0.1",
        "tai42-storage-local==1.1.14",
        "tai42-toolbox==4.0.1",
    ]


def test_manifest_strips_extras():
    assert derive.parse_manifest("tai42-skeleton[toolbox,files]==18.0.1\n") == [
        "tai42-skeleton==18.0.1"
    ]


def test_manifest_output_is_sorted():
    text = "tai42-kit==6.0.1\ntai42-contract==14.0.0\n"
    assert derive.parse_manifest(text) == [
        "tai42-contract==14.0.0",
        "tai42-kit==6.0.1",
    ]


def test_manifest_drops_comments_and_blank_lines():
    text = "# a header\n\ntai42-contract==14.0.0  # trailing note\n"
    assert derive.parse_manifest(text) == ["tai42-contract==14.0.0"]


def test_manifest_drops_non_first_party_lines():
    text = "msgpack>=1.2.1\nsetuptools>=78.1.1\ntai42-contract==14.0.0\n"
    assert derive.parse_manifest(text) == ["tai42-contract==14.0.0"]


def test_manifest_normalizes_name_and_case():
    assert derive.parse_manifest("Tai42_Contract==14.0.0\n") == [
        "tai42-contract==14.0.0"
    ]


def test_manifest_non_exact_pin_raises():
    with pytest.raises(derive.DeriveError, match="not an exact == pin"):
        derive.parse_manifest("tai42-contract>=14.0.0\n")


def test_manifest_bare_name_without_pin_raises():
    with pytest.raises(derive.DeriveError, match="not an exact == pin"):
        derive.parse_manifest("tai42-contract\n")


def test_manifest_extras_without_pin_raises():
    with pytest.raises(derive.DeriveError, match="not an exact == pin"):
        derive.parse_manifest("tai42-skeleton[toolbox]\n")


def test_manifest_conflicting_pins_raise():
    text = "tai42-contract==14.0.0\ntai42-contract==13.2.0\n"
    with pytest.raises(derive.DeriveError, match="two versions"):
        derive.parse_manifest(text)


def test_manifest_empty_raises():
    with pytest.raises(derive.DeriveError, match="no tai42-\\* pin"):
        derive.parse_manifest("msgpack>=1.2.1\n# only comments and non-first-party\n")


# --- in-venv emitter ----------------------------------------------------------


class _FakeDist:
    def __init__(self, name: str | None, version: str):
        self.metadata = {"Name": name}
        self.version = version


def _patch_distributions(monkeypatch, dists):
    monkeypatch.setattr(derive.metadata, "distributions", lambda: iter(dists))


def test_emit_from_venv_filters_and_sorts(monkeypatch):
    _patch_distributions(
        monkeypatch,
        [
            _FakeDist("tai42-kit", "6.0.1"),
            _FakeDist("pydantic", "2.9.0"),
            _FakeDist("tai42-contract", "14.0.0"),
            _FakeDist("packaging", "24.0"),
        ],
    )
    assert derive.emit_from_venv() == [
        "tai42-contract==14.0.0",
        "tai42-kit==6.0.1",
    ]


def test_emit_from_venv_normalizes_name(monkeypatch):
    _patch_distributions(monkeypatch, [_FakeDist("tai42_storage_local", "1.1.14")])
    assert derive.emit_from_venv() == ["tai42-storage-local==1.1.14"]


def test_emit_from_venv_none_installed_raises(monkeypatch):
    _patch_distributions(monkeypatch, [_FakeDist("pydantic", "2.9.0")])
    with pytest.raises(derive.DeriveError, match="no tai42-\\* distribution"):
        derive.emit_from_venv()


def test_emit_from_venv_missing_version_raises(monkeypatch):
    _patch_distributions(monkeypatch, [_FakeDist("tai42-contract", "")])
    with pytest.raises(derive.DeriveError, match="without a version"):
        derive.emit_from_venv()


def test_emit_from_venv_conflicting_versions_raise(monkeypatch):
    _patch_distributions(
        monkeypatch,
        [_FakeDist("tai42-contract", "14.0.0"), _FakeDist("tai42-contract", "13.2.0")],
    )
    with pytest.raises(derive.DeriveError, match="two versions"):
        derive.emit_from_venv()


# --- CLI ----------------------------------------------------------------------


def test_main_manifest_prints_sorted_pins(tmp_path, capsys):
    manifest = tmp_path / "req.txt"
    manifest.write_text("tai42-kit==6.0.1\ntai42-contract==14.0.0\n")
    assert derive.main(["--manifest", str(manifest)]) == 0
    assert capsys.readouterr().out == "tai42-contract==14.0.0\ntai42-kit==6.0.1\n"


def test_main_manifest_malformed_pin_exit_1(tmp_path, capsys):
    manifest = tmp_path / "req.txt"
    manifest.write_text("tai42-contract>=14.0.0\n")
    assert derive.main(["--manifest", str(manifest)]) == 1
    assert "not an exact == pin" in capsys.readouterr().err


def test_main_manifest_missing_file_exit_1(tmp_path, capsys):
    assert derive.main(["--manifest", str(tmp_path / "absent.txt")]) == 1
    assert "error:" in capsys.readouterr().err


def test_main_venv_mode(monkeypatch, capsys):
    _patch_distributions(monkeypatch, [_FakeDist("tai42-contract", "14.0.0")])
    assert derive.main([]) == 0
    assert capsys.readouterr().out == "tai42-contract==14.0.0\n"
