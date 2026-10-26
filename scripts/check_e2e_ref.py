#!/usr/bin/env python3
"""Gate the release image on its e2e suite tag existing in the suite repository.

`docker/E2E_REF` names the tai42-e2e release tag (`tai42-e2e-v<X>.<Y>.<Z>`) the
release pipeline runs against the platform image before it publishes it, and the
image carries it as its `ai.tai42.e2e.ref` label. The value must be a stable
release tag of the e2e member and must exist in the suite repository, else no
build is worth starting: the gate could not check the suite out. Existence is
proved with `git ls-remote --exit-code --tags <repo> refs/tags/<tag>`.

`--ref` checks a value given on the command line instead of the file (the e2e
gate checks the label it read from the image this way).

Exit 0 found, 1 malformed or not found, 2 any read failure (the file, or git
unable to read the repository). Every non-happy path prints a `::error::` line
naming the cause; no silent fallback.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

E2E_REF = re.compile(r"^tai42-e2e-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

# `git ls-remote --exit-code` exits 2 when the repository answered but no ref matched.
_LS_REMOTE_NO_MATCH = 2


class E2ERefError(Exception):
    """A read failure that cannot be resolved to a verdict."""


def is_e2e_ref(value: str) -> bool:
    """Whether `value` is a stable tai42-e2e release tag name."""
    return E2E_REF.fullmatch(value) is not None


def read_ref_file(path: str) -> str:
    """The tag name the ref file holds, surrounding whitespace removed."""
    return Path(path).read_text(encoding="utf-8").strip()


def tag_exists(repo_url: str, tag: str) -> bool:
    """Whether `refs/tags/<tag>` exists in the repository at `repo_url`."""
    ref = f"refs/tags/{tag}"
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--tags", repo_url, ref],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == _LS_REMOTE_NO_MATCH:
        return False
    if result.returncode != 0:
        raise E2ERefError(
            f"git ls-remote {repo_url} {ref} failed: {result.stderr.strip()}"
        )
    return any(line.split("\t")[-1] == ref for line in result.stdout.splitlines())


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--ref-file", default="docker/E2E_REF")
    source.add_argument("--ref")
    parser.add_argument("--suite-repo", default=os.environ.get("SUITE_REPO_URL"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    source = "--ref" if args.ref is not None else args.ref_file
    try:
        if not args.suite_repo:
            raise E2ERefError("--suite-repo is required (or set SUITE_REPO_URL)")
        tag = args.ref if args.ref is not None else read_ref_file(args.ref_file)
        if not is_e2e_ref(tag):
            print(
                f"::error::{source} holds {tag!r}, not a tai42-e2e release tag "
                f"(tai42-e2e-v<major>.<minor>.<patch>)"
            )
            return 1
        found = tag_exists(args.suite_repo, tag)
    except (E2ERefError, OSError) as exc:
        print(f"::error::{exc}")
        return 2

    if found:
        print(f"found: {source} = {tag} exists in {args.suite_repo}")
        return 0
    print(
        f"::error::{source} = {tag} not found in {args.suite_repo}: the e2e gate "
        f"cannot check the suite out at a tag the repository does not serve"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
