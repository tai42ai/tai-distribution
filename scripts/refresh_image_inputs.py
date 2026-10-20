#!/usr/bin/env python3
"""Recompute the release image's two inputs from the newest release tags.

The release image is built from two files that must move together:

- `docker/pypi-requirements.txt` — exact `tai42-<name>[extras]==<version>` pins.
  The target per package is the newest stable release tag
  (`tai42-<name>-v<version>`) in the monorepo. PyPI's JSON API sits behind a CDN
  cache and can lag a publish by minutes, so it is polled until it serves that
  version (or newer). Only already-pinned packages are touched — the roster
  itself never changes here.
- `docker/STUDIO_REF` — the full 40-hex commit sha of the newest stable
  `v<major>.<minor>.<patch>` release tag in the studio repository. An annotated
  tag is dereferenced to the commit it points at.

Both inputs are always recomputed from tags, never from the event that started
the run, so any run produces the same consistent pair and a newer run supersedes
an older one losslessly.

Every failure exits non-zero with a `::error::` line: a pinned package with no
release tag, a tagged version PyPI never serves, a studio repository with no
stable release tag, or a tag that does not resolve to a 40-hex commit sha.
Nothing is skipped silently.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

USER_AGENT = "tai-distribution-refresh-image-inputs"
PYPI_DEADLINE = 600  # seconds to wait for PyPI's index per package
PYPI_POLL_INTERVAL = 15

PIN = re.compile(r"^(tai42-[a-z0-9-]+)(\[[^]]*\])?==(\S+)$")
MONOREPO_TAG = re.compile(r"^refs/tags/(tai42-[a-z0-9-]+)-v(\S+)$")
STUDIO_TAG = re.compile(r"^refs/tags/v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA = re.compile(r"^[0-9a-f]{40}$")

VersionKey = tuple[tuple[int, ...], int]
GitHubGet = Callable[[str], Any]
PyPIGet = Callable[[str], "dict[str, Any] | None"]


class RefreshError(Exception):
    """A failure that leaves an image input without an authoritative value."""


def stable_key(version: str) -> VersionKey | None:
    """Sort key for a stable version, None for a pre-release.

    The local-version segment is split off first, then a trailing `.post<N>`: a
    post-release is a stable final, so it must clear the letter test that
    rejects a pre-release identifier (rc/a/b/dev). Its N then orders
    `X.Y.Z.postN` after `X.Y.Z`.
    """
    base = version.split("+")[0]
    post = re.search(r"\.post(\d+)$", base)
    release = base[: post.start()] if post else base
    if re.search(r"[a-zA-Z]", release.replace(".", "")):
        return None
    return (
        tuple(int(p) for p in release.split(".")),
        int(post.group(1)) if post else 0,
    )


def matching_refs(github_get: GitHubGet, repo: str, prefix: str) -> list[dict]:
    """Every tag ref of `repo` whose name starts with `prefix`.

    The matching-refs endpoint may serve every match in one response or honor
    page/per_page, so pages are walked until one contributes no new ref.
    """
    refs: list[dict] = []
    seen: set[str] = set()
    page = 1
    while True:
        batch = github_get(
            f"repos/{repo}/git/matching-refs/tags/{prefix}?per_page=100&page={page}"
        )
        new_refs = [r for r in batch if r["ref"] not in seen]
        if not new_refs:
            return refs
        for ref in new_refs:
            seen.add(ref["ref"])
            refs.append(ref)
        page += 1


def tagged_latest(github_get: GitHubGet, monorepo: str) -> dict[str, tuple]:
    """Newest stable tagged version per component, from the monorepo's tags."""
    latest: dict[str, tuple] = {}
    for ref in matching_refs(github_get, monorepo, "tai42-"):
        m = MONOREPO_TAG.match(ref["ref"])
        if not m:
            continue
        name, version = m.groups()
        key = stable_key(version)
        if key and (name not in latest or key > latest[name][0]):
            latest[name] = (key, version)
    return latest


def pypi_best(pypi_get: PyPIGet, name: str) -> tuple | None:
    """Newest stable, non-yanked version PyPI's index currently serves.

    None while the package is not visible on PyPI at all.
    """
    data = pypi_get(name)
    if data is None:
        return None
    best = None
    for version, files in data["releases"].items():
        if not files or all(f.get("yanked") for f in files):
            continue
        key = stable_key(version)
        if key and (best is None or key > best[0]):
            best = (key, version)
    return best


def refresh_pins(
    text: str,
    tags: dict[str, tuple],
    pypi_get: PyPIGet,
    monorepo: str,
    *,
    deadline: float = PYPI_DEADLINE,
    poll_interval: float = PYPI_POLL_INTERVAL,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Return the requirements text with every `tai42-*` pin at its target."""
    out = []
    for line in text.splitlines(keepends=True):
        m = PIN.match(line.strip())
        if not m:
            out.append(line)
            continue
        name, extras, current = m.groups()
        if name not in tags:
            raise RefreshError(f"no release tag for {name} in {monorepo}")
        target = tags[name]
        until = clock() + deadline
        while True:
            best = pypi_best(pypi_get, name)
            if best and best[0] >= target[0]:
                break
            if clock() > until:
                raise RefreshError(
                    f"{name} {target[1]} is tagged in {monorepo} but PyPI still "
                    f"serves {best[1] if best else 'nothing'} after {deadline}s"
                )
            print(f"waiting for {name}=={target[1]} on PyPI…")
            sleep(poll_interval)
        new = best[1]
        if new != current:
            print(f"{name}: {current} -> {new}")
        out.append(f"{name}{extras or ''}=={new}\n")
    return "".join(out)


def tag_commit(github_get: GitHubGet, repo: str, ref: dict) -> str:
    """The commit sha a tag ref points at, dereferencing annotated tags."""
    obj = ref["object"]
    seen: set[str] = set()
    while obj["type"] == "tag":
        if obj["sha"] in seen:
            raise RefreshError(f"{ref['ref']} in {repo} is a cyclic tag chain")
        seen.add(obj["sha"])
        obj = github_get(f"repos/{repo}/git/tags/{obj['sha']}")["object"]
    if obj["type"] != "commit":
        raise RefreshError(
            f"{ref['ref']} in {repo} points at a {obj['type']}, not a commit"
        )
    sha = obj["sha"]
    if not SHA.match(sha):
        raise RefreshError(
            f"{ref['ref']} in {repo} resolves to '{sha}', not a 40-hex commit sha"
        )
    return sha


def studio_release_commit(github_get: GitHubGet, studio_repo: str) -> tuple[str, str]:
    """(tag, commit sha) of the newest stable `v<semver>` tag in the studio repo."""
    newest = None
    for ref in matching_refs(github_get, studio_repo, "v"):
        m = STUDIO_TAG.match(ref["ref"])
        if not m:
            continue
        key = tuple(int(p) for p in m.groups())
        if newest is None or key > newest[0]:
            newest = (key, ref)
    if newest is None:
        raise RefreshError(f"no stable v<major>.<minor>.<patch> tag in {studio_repo}")
    ref = newest[1]
    return ref["ref"].removeprefix("refs/tags/"), tag_commit(github_get, studio_repo, ref)


def github_getter(token: str) -> GitHubGet:
    def get(path: str) -> Any:
        req = urllib.request.Request(
            f"https://api.github.com/{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    return get


def pypi_get(name: str) -> dict[str, Any] | None:
    req = urllib.request.Request(
        f"https://pypi.org/pypi/{name}/json",
        headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monorepo", default=os.environ.get("MONOREPO"))
    parser.add_argument("--studio-repo", default=os.environ.get("STUDIO_REPO"))
    parser.add_argument("--pins", default="docker/pypi-requirements.txt")
    parser.add_argument("--studio-ref-file", default="docker/STUDIO_REF")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    github_get: GitHubGet | None = None,
    pypi: PyPIGet = pypi_get,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> int:
    args = _parse_args(argv)
    try:
        if not args.monorepo:
            raise RefreshError("--monorepo is required (or set MONOREPO)")
        if not args.studio_repo:
            raise RefreshError("--studio-repo is required (or set STUDIO_REPO)")
        if github_get is None:
            token = os.environ.get("GH_TOKEN")
            if not token:
                raise RefreshError("GH_TOKEN is required to read release tags")
            github_get = github_getter(token)

        pins_path = Path(args.pins)
        pins = refresh_pins(
            pins_path.read_text(encoding="utf-8"),
            tagged_latest(github_get, args.monorepo),
            pypi,
            args.monorepo,
            clock=clock,
            sleep=sleep,
        )

        tag, sha = studio_release_commit(github_get, args.studio_repo)
        ref_path = Path(args.studio_ref_file)
        current_ref = ref_path.read_text(encoding="utf-8").strip()
        if sha != current_ref:
            print(f"STUDIO_REF: {current_ref} -> {sha} ({tag})")
    except RefreshError as exc:
        print(f"::error::{exc}")
        return 1

    pins_path.write_text(pins, encoding="utf-8")
    ref_path.write_text(f"{sha}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
