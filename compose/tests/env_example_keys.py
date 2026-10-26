#!/usr/bin/env python3
"""compose ``.env.example`` keys vs the compose file that reads them.

Compose injects a ``.env`` value only for the keys ``docker-compose.yml``
interpolates (``${KEY}``, ``${KEY:-default}``, ...). Every key the deployment
section of ``.env.example`` offers, set or commented out, must therefore be
interpolated somewhere in ``docker-compose.yml``: a key it never reads is a
setting the operator fills in and the stack silently drops.

The deployment section ends at the ``Optional app config the compose does NOT
preset`` heading; the keys listed after it belong in ``config/.env``, which the
app reads itself, so they are not checked.

Run: python compose/tests/env_example_keys.py   (needs pyyaml).
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

COMPOSE_DIR = pathlib.Path(__file__).resolve().parent.parent
COMPOSE_FILE = COMPOSE_DIR / "docker-compose.yml"
ENV_EXAMPLE = COMPOSE_DIR / ".env.example"
APP_CONFIG_HEADING = "Optional app config the compose does NOT preset"

# `KEY=value` or a commented-out `# KEY=value` offered for the operator to set.
_ASSIGNMENT = re.compile(r"^(?:#\s?)?([A-Z][A-Z0-9_]*)=")
_INTERPOLATION = re.compile(r"\$\{([A-Z][A-Z0-9_]*)[:?}-]")


def offered_keys(text: str) -> set[str]:
    lines = text.splitlines()
    end = next((i for i, line in enumerate(lines) if APP_CONFIG_HEADING in line), None)
    if end is None:
        raise SystemExit(f"FAIL: heading {APP_CONFIG_HEADING!r} not found in {ENV_EXAMPLE.name}")
    return {match.group(1) for line in lines[:end] if (match := _ASSIGNMENT.match(line))}


def read_keys(node: object, *, in_environment: bool = False) -> set[str]:
    """The ``.env`` keys ``node`` reads.

    A key is read when a value interpolates it, or when a service ``environment``
    mapping lists it with no value: Compose then passes the ``.env`` value through
    unchanged (and leaves the key out when ``.env`` does not set it).
    """
    if isinstance(node, str):
        return set(_INTERPOLATION.findall(node))
    keys: set[str] = set()
    if isinstance(node, dict):
        for name, value in node.items():
            if in_environment and value is None:
                keys.add(name)
            keys |= read_keys(value, in_environment=name == "environment")
    elif isinstance(node, list):
        for item in node:
            keys |= read_keys(item)
    return keys


def main() -> int:
    offered = offered_keys(ENV_EXAMPLE.read_text())
    read = read_keys(yaml.safe_load(COMPOSE_FILE.read_text()))
    dropped = sorted(offered - read)
    if dropped:
        print(f"FAIL: {ENV_EXAMPLE.name} offers keys {COMPOSE_FILE.name} never reads:", file=sys.stderr)
        for key in dropped:
            print(f"  {key}", file=sys.stderr)
        return 1
    print(f"OK: all {len(offered)} keys {ENV_EXAMPLE.name} offers are read by {COMPOSE_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
