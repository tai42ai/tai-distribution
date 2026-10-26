"""Tests for check_e2e_ref: synthetic fixtures only, no network.

The ls-remote path runs against a local repo created in tmp_path carrying
synthetic tags.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_e2e_ref as gate
import pytest

# --- format -------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref", ["tai42-e2e-v24.0.1", "tai42-e2e-v0.1.0", "tai42-e2e-v100.20.3"]
)
def test_valid_ref_accepted(ref):
    assert gate.is_e2e_ref(ref)


@pytest.mark.parametrize(
    "ref",
    [
        "tai42-e2e-v24.0.1rc1",  # prerelease
        "tai42-e2e-v24.0.1-rc.1",  # prerelease, semver spelling
        "tai42-e2e-v24.0.1.post1",  # post-release
        "24.0.1",  # bare version
        "v24.0.1",  # studio-shaped tag
        "tai42-e2e-v24.0",  # two-part version
        "tai42-e2e-v24.0.1 trailing",  # trailing text
        "tai42-e2e-v24.0.1\nmain",  # second line
        "tai42-kit-v24.0.1",  # another member's tag
        "refs/tags/tai42-e2e-v24.0.1",  # full ref, not a tag name
        "main",  # branch
        "",
    ],
)
def test_invalid_ref_rejected(ref):
    assert not gate.is_e2e_ref(ref)


def test_read_ref_file_strips_the_newline(tmp_path):
    path = tmp_path / "E2E_REF"
    path.write_text("tai42-e2e-v24.0.1\n")
    assert gate.read_ref_file(str(path)) == "tai42-e2e-v24.0.1"


# --- local git path -----------------------------------------------------------


def _make_suite_repo(root: Path, *tags: str) -> None:
    """Init a local repo with one commit carrying each tag (annotated and light)."""
    root.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    (root / "README").write_text("suite\n")
    git("add", "-A")
    git("commit", "-q", "-m", "suite")
    for i, tag in enumerate(tags):
        if i % 2:
            git("tag", "-a", tag, "-m", tag)
        else:
            git("tag", tag)


def test_tag_exists_finds_light_and_annotated_tags(tmp_path):
    repo = tmp_path / "suite"
    _make_suite_repo(repo, "tai42-e2e-v24.0.0", "tai42-e2e-v24.0.1")
    assert gate.tag_exists(str(repo), "tai42-e2e-v24.0.0")
    assert gate.tag_exists(str(repo), "tai42-e2e-v24.0.1")


def test_tag_exists_false_for_an_absent_tag(tmp_path):
    repo = tmp_path / "suite"
    _make_suite_repo(repo, "tai42-e2e-v24.0.0")
    assert not gate.tag_exists(str(repo), "tai42-e2e-v24.0.1")


def test_tag_exists_does_not_match_a_longer_tag(tmp_path):
    repo = tmp_path / "suite"
    _make_suite_repo(repo, "tai42-e2e-v24.0.10")
    assert not gate.tag_exists(str(repo), "tai42-e2e-v24.0.1")


def test_tag_exists_raises_when_git_cannot_read_the_repo(tmp_path):
    with pytest.raises(gate.E2ERefError, match="git ls-remote"):
        gate.tag_exists(str(tmp_path / "absent"), "tai42-e2e-v24.0.1")


# --- CLI end to end -----------------------------------------------------------


def _inputs(tmp_path: Path, ref_text: str, *tags: str):
    repo = tmp_path / "suite"
    _make_suite_repo(repo, *tags)
    ref_file = tmp_path / "E2E_REF"
    ref_file.write_text(ref_text)
    return repo, ref_file


def test_main_found_exit_0(tmp_path, capsys):
    repo, ref_file = _inputs(tmp_path, "tai42-e2e-v24.0.1\n", "tai42-e2e-v24.0.1")
    assert gate.main(["--ref-file", str(ref_file), "--suite-repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("found:")
    assert "tai42-e2e-v24.0.1" in out


def test_main_ref_value_found_exit_0(tmp_path, capsys):
    repo, _ = _inputs(tmp_path, "", "tai42-e2e-v24.0.1")
    assert gate.main(["--ref", "tai42-e2e-v24.0.1", "--suite-repo", str(repo)]) == 0
    assert capsys.readouterr().out.startswith("found:")


def test_main_absent_tag_exit_1(tmp_path, capsys):
    repo, ref_file = _inputs(tmp_path, "tai42-e2e-v24.0.2\n", "tai42-e2e-v24.0.1")
    assert gate.main(["--ref-file", str(ref_file), "--suite-repo", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "tai42-e2e-v24.0.2" in out and "not found" in out


def test_main_malformed_ref_exit_1_without_asking_git(tmp_path, capsys):
    ref_file = tmp_path / "E2E_REF"
    ref_file.write_text("tai42-e2e-v24.0.1rc1\n")
    # The suite repo does not exist: a malformed value must be refused before git runs.
    argv = ["--ref-file", str(ref_file), "--suite-repo", str(tmp_path / "absent")]
    assert gate.main(argv) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "not a tai42-e2e release tag" in out


def test_main_empty_ref_value_exit_1(tmp_path, capsys):
    assert gate.main(["--ref", "", "--suite-repo", str(tmp_path / "absent")]) == 1
    assert "not a tai42-e2e release tag" in capsys.readouterr().out


def test_main_missing_ref_file_exit_2(tmp_path, capsys):
    argv = ["--ref-file", str(tmp_path / "absent"), "--suite-repo", str(tmp_path)]
    assert gate.main(argv) == 2
    assert "::error::" in capsys.readouterr().out


def test_main_git_failure_exit_2(tmp_path, capsys):
    ref_file = tmp_path / "E2E_REF"
    ref_file.write_text("tai42-e2e-v24.0.1\n")
    argv = ["--ref-file", str(ref_file), "--suite-repo", str(tmp_path / "absent")]
    assert gate.main(argv) == 2
    assert "::error::git ls-remote" in capsys.readouterr().out


def test_main_requires_the_suite_repo(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("SUITE_REPO_URL", raising=False)
    ref_file = tmp_path / "E2E_REF"
    ref_file.write_text("tai42-e2e-v24.0.1\n")
    assert gate.main(["--ref-file", str(ref_file)]) == 2
    assert "::error::--suite-repo is required" in capsys.readouterr().out
