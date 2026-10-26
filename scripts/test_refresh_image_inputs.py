"""Tests for refresh_image_inputs: pin, studio-ref and e2e-ref recomputation from tags.

GitHub and PyPI are replaced by in-memory fakes built from synthetic refs and
release listings; no test touches the network or sleeps.
"""

from __future__ import annotations

import pytest
import refresh_image_inputs as refresh

MONOREPO = "acme/tai42"
STUDIO = "acme/tai-studio"

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
TAG_OBJ = "d" * 40


def ref(name: str, sha: str, kind: str = "commit") -> dict:
    return {"ref": f"refs/tags/{name}", "object": {"type": kind, "sha": sha}}


class FakeGitHub:
    """Serves matching-refs pages and annotated tag objects from fixtures.

    `pages` maps a repo to a list of pages; a page past the end repeats the last
    one, as the endpoint does when it ignores page/per_page.
    """

    def __init__(self, pages: dict[str, dict[str, list[list[dict]]]], tags=None):
        self.pages = pages
        self.tags = tags or {}
        self.calls: list[str] = []

    def __call__(self, path: str):
        self.calls.append(path)
        repo, _, rest = path.removeprefix("repos/").partition("/git/")
        if rest.startswith("matching-refs/tags/"):
            prefix, _, query = rest.removeprefix("matching-refs/tags/").partition("?")
            page = int(query.split("page=")[-1])
            pages = self.pages[repo][prefix]
            return pages[min(page, len(pages)) - 1]
        if rest.startswith("tags/"):
            return self.tags[rest.removeprefix("tags/")]
        raise AssertionError(f"unexpected GitHub path {path}")


def releases(*versions: str, yanked: tuple[str, ...] = ()) -> dict:
    return {
        "releases": {
            v: [{"yanked": v in yanked}] for v in versions
        }
    }


class FakePyPI:
    """Serves a scripted sequence of PyPI responses per package."""

    def __init__(self, responses: dict[str, list]):
        self.responses = responses
        self.calls: dict[str, int] = {}

    def __call__(self, name: str):
        n = self.calls.get(name, 0)
        self.calls[name] = n + 1
        seq = self.responses[name]
        return seq[min(n, len(seq) - 1)]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


# --- stable_key ---------------------------------------------------------------


@pytest.mark.parametrize("version", ["1.2.3rc1", "1.0.0a1", "2.0.0b2", "3.0.0.dev4"])
def test_stable_key_rejects_prereleases(version):
    assert refresh.stable_key(version) is None


def test_stable_key_orders_post_after_final_and_ignores_local():
    assert refresh.stable_key("1.2.3.post2") > refresh.stable_key("1.2.3")
    assert refresh.stable_key("1.2.3+local") == refresh.stable_key("1.2.3")
    assert refresh.stable_key("1.10.0") > refresh.stable_key("1.9.9")


# --- monorepo tags ------------------------------------------------------------


def test_tagged_latest_picks_newest_stable_per_package_across_pages():
    gh = FakeGitHub(
        {
            MONOREPO: {
                "tai42-": [
                    [
                        ref("tai42-contract-v20.0.0", SHA_A),
                        ref("tai42-contract-v21.0.0", SHA_B),
                    ],
                    [
                        ref("tai42-contract-v22.0.0rc1", SHA_C),
                        ref("tai42-kit-v3.1.0", SHA_A),
                        ref("not-a-release", SHA_A),
                    ],
                ]
            }
        }
    )
    latest = refresh.tagged_latest(gh, MONOREPO)
    assert {k: v[1] for k, v in latest.items()} == {
        "tai42-contract": "21.0.0",
        "tai42-kit": "3.1.0",
    }


# --- pins ---------------------------------------------------------------------

PINS = """\
# First-party package set.
tai42-contract==20.0.0
tai42-skeleton[toolbox,files]==18.0.1
"""

TAGS = {
    "tai42-contract": (refresh.stable_key("21.0.0"), "21.0.0"),
    "tai42-skeleton": (refresh.stable_key("18.0.1"), "18.0.1"),
}


def test_refresh_pins_rewrites_to_tagged_version_keeping_extras_and_comments():
    pypi = FakePyPI(
        {
            "tai42-contract": [releases("20.0.0", "21.0.0", "22.0.0rc1")],
            "tai42-skeleton": [releases("18.0.1")],
        }
    )
    out = refresh.refresh_pins(PINS, TAGS, pypi, MONOREPO, sleep=pytest.fail)
    assert out == (
        "# First-party package set.\n"
        "tai42-contract==21.0.0\n"
        "tai42-skeleton[toolbox,files]==18.0.1\n"
    )


def test_refresh_pins_waits_for_pypi_to_catch_up_with_the_tag():
    clock = Clock()
    pypi = FakePyPI(
        {
            "tai42-contract": [None, releases("20.0.0"), releases("20.0.0", "21.0.0")],
            "tai42-skeleton": [releases("18.0.1")],
        }
    )
    out = refresh.refresh_pins(
        PINS, TAGS, pypi, MONOREPO, clock=clock, sleep=clock.sleep
    )
    assert "tai42-contract==21.0.0\n" in out
    assert pypi.calls["tai42-contract"] == 3


def test_refresh_pins_skips_yanked_releases():
    pypi = FakePyPI(
        {
            "tai42-contract": [releases("21.0.0", "21.0.1", yanked=("21.0.1",))],
            "tai42-skeleton": [releases("18.0.1")],
        }
    )
    out = refresh.refresh_pins(PINS, TAGS, pypi, MONOREPO, sleep=pytest.fail)
    assert "tai42-contract==21.0.0\n" in out


def test_refresh_pins_fails_when_pypi_never_serves_the_tagged_version():
    clock = Clock()
    pypi = FakePyPI(
        {"tai42-contract": [releases("20.0.0")], "tai42-skeleton": [releases("18.0.1")]}
    )
    with pytest.raises(refresh.RefreshError, match="PyPI still serves 20.0.0"):
        refresh.refresh_pins(
            PINS, TAGS, pypi, MONOREPO, clock=clock, sleep=clock.sleep
        )


def test_refresh_pins_fails_for_a_pinned_package_without_a_tag():
    pypi = FakePyPI({"tai42-contract": [releases("21.0.0")]})
    with pytest.raises(refresh.RefreshError, match="no release tag for tai42-skeleton"):
        refresh.refresh_pins(
            PINS, {"tai42-contract": TAGS["tai42-contract"]}, pypi, MONOREPO,
            sleep=pytest.fail,
        )


# --- studio ref ---------------------------------------------------------------


def test_studio_release_commit_is_newest_semver_tag_not_newest_listed():
    gh = FakeGitHub(
        {
            STUDIO: {
                "v": [
                    [
                        ref("v22.10.0", SHA_C),
                        ref("v22.4.1", SHA_B),
                        ref("v9.3.0", SHA_A),
                        ref("v23.0.0-rc.1", SHA_A),
                        ref("v23", SHA_A),
                    ]
                ]
            }
        }
    )
    assert refresh.studio_release_commit(gh, STUDIO) == ("v22.10.0", SHA_C)


def test_studio_release_commit_dereferences_annotated_tags():
    gh = FakeGitHub(
        {STUDIO: {"v": [[ref("v22.4.1", TAG_OBJ, kind="tag")]]}},
        tags={TAG_OBJ: {"object": {"type": "commit", "sha": SHA_B}}},
    )
    assert refresh.studio_release_commit(gh, STUDIO) == ("v22.4.1", SHA_B)
    assert f"repos/{STUDIO}/git/tags/{TAG_OBJ}" in gh.calls


def test_studio_release_commit_fails_without_a_stable_tag():
    gh = FakeGitHub({STUDIO: {"v": [[ref("v1.0.0-beta.1", SHA_A)]]}})
    with pytest.raises(refresh.RefreshError, match="no stable"):
        refresh.studio_release_commit(gh, STUDIO)


def test_studio_release_commit_fails_when_tag_points_at_a_non_commit():
    gh = FakeGitHub({STUDIO: {"v": [[ref("v1.0.0", SHA_A, kind="tree")]]}})
    with pytest.raises(refresh.RefreshError, match="not a commit"):
        refresh.studio_release_commit(gh, STUDIO)


def test_studio_release_commit_fails_on_a_malformed_sha():
    gh = FakeGitHub({STUDIO: {"v": [[ref("v1.0.0", "abc123")]]}})
    with pytest.raises(refresh.RefreshError, match="not a 40-hex commit sha"):
        refresh.studio_release_commit(gh, STUDIO)


# --- e2e ref ------------------------------------------------------------------


def _tags(*names: str) -> dict[str, tuple]:
    gh = FakeGitHub({MONOREPO: {"tai42-": [[ref(n, SHA_A) for n in names]]}})
    return refresh.tagged_latest(gh, MONOREPO)


def test_e2e_release_tag_is_the_newest_stable_suite_tag():
    tags = _tags(
        "tai42-e2e-v23.0.2",
        "tai42-e2e-v24.0.1",
        "tai42-e2e-v24.0.0",
        "tai42-e2e-v25.0.0rc1",
        "tai42-contract-v26.0.0",
    )
    assert refresh.e2e_release_tag(tags, MONOREPO) == "tai42-e2e-v24.0.1"


def test_e2e_release_tag_fails_without_a_suite_tag():
    tags = _tags("tai42-contract-v26.0.0", "tai42-e2e-v25.0.0rc1")
    with pytest.raises(refresh.RefreshError, match="no release tag for tai42-e2e"):
        refresh.e2e_release_tag(tags, MONOREPO)


def test_e2e_release_tag_fails_when_the_newest_is_not_a_plain_release():
    tags = _tags("tai42-e2e-v24.0.1", "tai42-e2e-v24.0.1.post1")
    with pytest.raises(refresh.RefreshError, match="not a plain"):
        refresh.e2e_release_tag(tags, MONOREPO)


# --- main ---------------------------------------------------------------------


def _repo_files(tmp_path, pins=PINS, studio_ref=SHA_A, e2e_ref="tai42-e2e-v9.0.0"):
    pins_file = tmp_path / "pypi-requirements.txt"
    pins_file.write_text(pins)
    ref_file = tmp_path / "STUDIO_REF"
    ref_file.write_text(f"{studio_ref}\n")
    e2e_file = tmp_path / "E2E_REF"
    e2e_file.write_text(f"{e2e_ref}\n")
    argv = [
        "--monorepo", MONOREPO, "--studio-repo", STUDIO,
        "--pins", str(pins_file), "--studio-ref-file", str(ref_file),
        "--e2e-ref-file", str(e2e_file),
    ]
    return pins_file, ref_file, e2e_file, argv


def _github(studio_tags, e2e_tags=("tai42-e2e-v9.0.0", "tai42-e2e-v9.1.0")):
    return FakeGitHub(
        {
            MONOREPO: {
                "tai42-": [
                    [
                        ref("tai42-contract-v21.0.0", SHA_A),
                        ref("tai42-skeleton-v18.0.1", SHA_A),
                        *(ref(tag, SHA_A) for tag in e2e_tags),
                    ]
                ]
            },
            STUDIO: {"v": [studio_tags]},
        }
    )


PYPI_OK = {
    "tai42-contract": [releases("20.0.0", "21.0.0")],
    "tai42-skeleton": [releases("18.0.1")],
}


def test_main_rewrites_all_three_inputs_together(tmp_path, capsys):
    pins_file, ref_file, e2e_file, argv = _repo_files(tmp_path)
    rc = refresh.main(
        argv,
        github_get=_github([ref("v22.4.0", SHA_A), ref("v22.4.1", SHA_B)]),
        pypi=FakePyPI(PYPI_OK),
        sleep=pytest.fail,
    )
    assert rc == 0
    assert "tai42-contract==21.0.0\n" in pins_file.read_text()
    assert ref_file.read_text() == f"{SHA_B}\n"
    assert e2e_file.read_text() == "tai42-e2e-v9.1.0\n"
    out = capsys.readouterr().out
    assert "tai42-contract: 20.0.0 -> 21.0.0" in out
    assert f"STUDIO_REF: {SHA_A} -> {SHA_B} (v22.4.1)" in out
    assert "E2E_REF: tai42-e2e-v9.0.0 -> tai42-e2e-v9.1.0" in out


def test_main_leaves_files_byte_identical_when_already_current(tmp_path, capsys):
    current = "tai42-contract==21.0.0\ntai42-skeleton[toolbox,files]==18.0.1\n"
    pins_file, ref_file, e2e_file, argv = _repo_files(
        tmp_path, pins=current, studio_ref=SHA_B, e2e_ref="tai42-e2e-v9.1.0"
    )
    rc = refresh.main(
        argv,
        github_get=_github([ref("v22.4.1", SHA_B)]),
        pypi=FakePyPI(PYPI_OK),
        sleep=pytest.fail,
    )
    assert rc == 0
    assert pins_file.read_text() == current
    assert ref_file.read_text() == f"{SHA_B}\n"
    assert e2e_file.read_text() == "tai42-e2e-v9.1.0\n"
    assert "E2E_REF" not in capsys.readouterr().out


def test_main_writes_nothing_when_the_studio_side_fails(tmp_path, capsys):
    pins_file, ref_file, e2e_file, argv = _repo_files(tmp_path)
    rc = refresh.main(
        argv,
        github_get=_github([ref("v1.0.0-rc.1", SHA_B)]),
        pypi=FakePyPI(PYPI_OK),
        sleep=pytest.fail,
    )
    assert rc == 1
    assert "::error::no stable" in capsys.readouterr().out
    assert pins_file.read_text() == PINS
    assert ref_file.read_text() == f"{SHA_A}\n"
    assert e2e_file.read_text() == "tai42-e2e-v9.0.0\n"


def test_main_writes_nothing_when_the_e2e_side_fails(tmp_path, capsys):
    pins_file, ref_file, e2e_file, argv = _repo_files(tmp_path)
    rc = refresh.main(
        argv,
        github_get=_github([ref("v22.4.1", SHA_B)], e2e_tags=()),
        pypi=FakePyPI(PYPI_OK),
        sleep=pytest.fail,
    )
    assert rc == 1
    assert "::error::no release tag for tai42-e2e" in capsys.readouterr().out
    assert pins_file.read_text() == PINS
    assert ref_file.read_text() == f"{SHA_A}\n"
    assert e2e_file.read_text() == "tai42-e2e-v9.0.0\n"


def test_main_requires_the_repositories(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MONOREPO", raising=False)
    monkeypatch.delenv("STUDIO_REPO", raising=False)
    pins_file, ref_file, e2e_file, _ = _repo_files(tmp_path)
    rc = refresh.main(
        [
            "--pins", str(pins_file), "--studio-ref-file", str(ref_file),
            "--e2e-ref-file", str(e2e_file),
        ],
        github_get=_github([]),
        pypi=FakePyPI(PYPI_OK),
    )
    assert rc == 1
    assert "--monorepo is required" in capsys.readouterr().out
