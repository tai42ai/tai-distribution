#!/usr/bin/env python3
"""Gate the release image on platform/studio contract-range agreement.

The release image installs `tai42-contract` from PyPI at the `==` pin in
`docker/pypi-requirements.txt` and builds the studio SPA from the tai-studio
commit in `docker/STUDIO_REF`. That commit declares, in its docs-demo marketplace
registry fixture (`_SPEC["contract"]`), the single contract range the studio
supports. The pinned contract version must lie inside the contract range the
pinned studio commit declares, else the served SPA speaks a contract the running
platform does not. The studio declaration is fetched from the studio repository by
commit, as the Dockerfile fetches the studio source, read at FETCH_HEAD and loaded
with the studio's own importlib idiom; membership is tested with PEP 440.

Exit 0 inside the range, 1 outside, 2 any read/load/parse failure. Every non-happy
path prints a `::error::` line naming the cause; no silent fallback.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


class AlignmentError(Exception):
    """A read/load/parse failure that cannot be resolved to a verdict."""


_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?==(\S+)$")


def _normalize(name: str) -> str:
    """PEP 503 name normalization so a pin matches regardless of separator case."""
    return re.sub(r"[-_.]+", "-", name).lower()


def contract_pin(text: str, package: str) -> str:
    """Return the single `==` pin of `package` in a requirements file's text.

    Zero or more than one match is a failure — the manifest is ambiguous.
    """
    target = _normalize(package)
    matches: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _PIN_RE.match(line)
        if m and _normalize(m.group(1)) == target:
            matches.append(m.group(3))
    if not matches:
        raise AlignmentError(f"no {package} pin found in the pins file")
    if len(matches) > 1:
        raise AlignmentError(
            f"{package} is pinned {len(matches)} times in the pins file: {matches}"
        )
    return matches[0]


def declared_range(module_text: str) -> str:
    """Load the studio registry module and return its `_SPEC['contract']` range.

    Uses the studio check's own importlib idiom on the fetched module text.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "studio_registry.py"
        path.write_text(module_text, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("studio_registry", path)
        if spec is None or spec.loader is None:
            raise AlignmentError("could not load the studio registry module")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise AlignmentError(
                f"studio registry module failed to load: {exc}"
            ) from exc
    declaration = getattr(module, "_SPEC", None)
    if not isinstance(declaration, dict) or "contract" not in declaration:
        raise AlignmentError("studio registry module has no _SPEC['contract']")
    value = declaration["contract"]
    if not isinstance(value, str):
        raise AlignmentError("studio registry _SPEC['contract'] is not a string")
    return value


@dataclass(frozen=True)
class Verdict:
    aligned: bool
    pinned_version: str
    contract_range: str


def verdict(pinned_version: str, contract_range: str) -> Verdict:
    """Test PEP 440 membership of the pinned version in the declared range."""
    try:
        specifier = SpecifierSet(contract_range)
    except InvalidSpecifier as exc:
        raise AlignmentError(
            f"studio declares an unparsable contract range {contract_range!r}: {exc}"
        ) from exc
    try:
        version = Version(pinned_version)
    except InvalidVersion as exc:
        raise AlignmentError(
            f"unparsable pinned version {pinned_version!r}: {exc}"
        ) from exc
    return Verdict(
        aligned=specifier.contains(version, prereleases=True),
        pinned_version=pinned_version,
        contract_range=contract_range,
    )


def read_studio_ref(path: str) -> str:
    """Read a 40-hex commit sha from the studio-ref file, validated."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not _SHA_RE.match(text):
        raise AlignmentError(f"{path} does not hold a 40-hex commit sha: {text!r}")
    return text


def read_studio_range_file(repo_url: str, sha: str, path: str) -> str:
    """Fetch a single studio commit and return the range file's text at it.

    Fetches the commit the way the Dockerfile fetches the studio source (init,
    fetch by commit, read at FETCH_HEAD), so the declaration the gate reads is the
    one the image builds against.
    """
    with tempfile.TemporaryDirectory() as tmp:

        def git(*args: str) -> str:
            result = subprocess.run(
                ["git", "-C", tmp, *args],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise AlignmentError(
                    f"git {' '.join(args)} failed: {result.stderr.strip()}"
                )
            return result.stdout

        git("init", "-q")
        git("remote", "add", "origin", repo_url)
        git("fetch", "--depth", "1", "origin", sha)
        return git("show", f"FETCH_HEAD:{path}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pins", default="docker/pypi-requirements.txt")
    parser.add_argument("--contract-package", default="tai42-contract")
    parser.add_argument("--studio-ref-file", default="docker/STUDIO_REF")
    parser.add_argument("--studio-repo", default=os.environ.get("STUDIO_REPO_URL"))
    parser.add_argument(
        "--studio-range-file",
        default="e2e/docs-demo/marketplace-registry.py",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if not args.studio_repo:
            raise AlignmentError("--studio-repo is required (or set STUDIO_REPO_URL)")
        pins_text = Path(args.pins).read_text(encoding="utf-8")
        pinned_version = contract_pin(pins_text, args.contract_package)
        sha = read_studio_ref(args.studio_ref_file)
        module_text = read_studio_range_file(
            args.studio_repo, sha, args.studio_range_file
        )
        contract_range = declared_range(module_text)
        result = verdict(pinned_version, contract_range)
    except (AlignmentError, OSError) as exc:
        print(f"::error::{exc}")
        return 2

    pkg = args.contract_package
    if result.aligned:
        print(
            f"aligned: {args.pins} pins {pkg}=={pinned_version}, inside the "
            f"contract range the studio at {args.studio_ref_file} = {sha} "
            f"declares ({pkg} {contract_range})"
        )
        return 0

    print(
        f"::error::platform and studio not aligned: {args.pins} pins "
        f"{pkg}=={pinned_version} but the studio at {args.studio_ref_file} = "
        f"{sha} declares supported {pkg} {contract_range}; the image waits for "
        f"the release that brings the two into range"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
