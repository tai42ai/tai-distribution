#!/usr/bin/env python3
"""Emit the image's first-party core pins as a pip/uv constraints file.

The image ships its core versions as a constraints file and sets
`PIP_CONSTRAINT`/`UV_CONSTRAINT` to it, so every pip/uv-pip install into the
venv is held to those versions: a package that needs a different core major is
refused loudly by the resolver instead of silently downgrading the core.

Two modes, one source of truth for what the image contains:

- Default (in-venv emitter): list the `tai42-*` distributions actually installed
  in the running interpreter's environment via `importlib.metadata` and emit one
  sorted `name==version` line per package. This is what the Dockerfile runs with
  the venv's interpreter after the venv is copied in, so the constraints file
  equals the installed versions by construction in every build mode.

- `--manifest <path>` (manifest parser): read a requirements file, keep its
  `tai42-*` lines, strip any `[extras]` group (a constraints file cannot carry
  extras), and emit the same sorted `name==version` lines. The CI assertion runs
  this on the host to prove the shipped file equals the release manifest's pins.

Every failure raises and exits non-zero: no `tai42-*` distribution found, a
distribution without a version, or a `tai42-*` manifest line that is not an
exact `==` pin. Nothing is skipped silently.
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata
from pathlib import Path

_FIRST_PARTY_PREFIX = "tai42-"
# A requirement line's leading distribution name (before any extras or specifier).
_NAME_RE = re.compile(r"^([A-Za-z0-9._-]+)")
# An exact pin, optionally carrying an extras group: name[extra1,extra2]==version.
_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(\S+)$")


class DeriveError(Exception):
    """A condition that cannot yield a valid constraint line."""


def _normalize(name: str) -> str:
    """PEP 503 name normalization, so a pin matches regardless of separator/case."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _is_first_party(name: str) -> bool:
    return _normalize(name).startswith(_FIRST_PARTY_PREFIX)


def _format(pins: dict[str, str]) -> list[str]:
    """One sorted `name==version` line per package, names PEP 503 normalized."""
    return [f"{name}=={pins[name]}" for name in sorted(pins)]


def emit_from_venv() -> list[str]:
    """The `tai42-*` distributions installed in the current environment."""
    pins: dict[str, str] = {}
    for dist in metadata.distributions():
        raw_name = dist.metadata["Name"]
        if raw_name is None or not _is_first_party(raw_name):
            continue
        name = _normalize(raw_name)
        version = dist.version
        if not version:
            raise DeriveError(f"{name} is installed without a version")
        existing = pins.get(name)
        if existing is not None and existing != version:
            raise DeriveError(
                f"{name} is installed at two versions: {existing} and {version}"
            )
        pins[name] = version
    if not pins:
        raise DeriveError(
            "no tai42-* distribution is installed in this environment; "
            "the constraints file would be empty"
        )
    return _format(pins)


def parse_manifest(text: str) -> list[str]:
    """The extras-stripped `tai42-*` `==` pins from a requirements file's text."""
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name_match = _NAME_RE.match(line)
        if name_match is None or not _is_first_party(name_match.group(1)):
            continue
        pin_match = _PIN_RE.match(line)
        if pin_match is None:
            raise DeriveError(
                f"first-party requirement is not an exact == pin: {line!r}"
            )
        name = _normalize(pin_match.group(1))
        version = pin_match.group(2)
        existing = pins.get(name)
        if existing is not None and existing != version:
            raise DeriveError(
                f"{name} is pinned to two versions: {existing} and {version}"
            )
        pins[name] = version
    if not pins:
        raise DeriveError("no tai42-* pin found in the manifest")
    return _format(pins)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        help=(
            "parse this requirements file instead of the installed environment "
            "(the host-side CI assertion)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.manifest is not None:
            lines = parse_manifest(Path(args.manifest).read_text(encoding="utf-8"))
        else:
            lines = emit_from_venv()
    except (DeriveError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
