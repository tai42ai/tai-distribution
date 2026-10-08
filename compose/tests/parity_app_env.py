#!/usr/bin/env python3
"""compose pinned-set parity: the ``x-tai-app-env`` anchor vs its declared pinned keys.

The anchor declares the keys the deployment pins in ``TAI_SUPERVISED_PINNED_KEYS`` (a
JSON list the platform's recycle refusal reads). Every app service reuses this one
anchor, so its key set IS the compose deployment-value pinning: this asserts the
declared list is EXACTLY the anchor's keys, so an anchor edit not mirrored in the
declared list (or vice versa) fails CI.

The deployment-infra bare reads (``X_CLASSIFIED_DEPLOYMENT_BARE_READS`` — the shape
marker, the pinned-set variable and the sentinel path) are refused on the X axis at
every env writer, not pinned, so they are excluded from the anchor side of the compare.

Run: python compose/tests/parity_app_env.py   (needs tai42-skeleton importable —
see the CI job).
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

from tai42_skeleton.config.recycle_policy import (
    PINNED_KEYS_ENV,
    X_CLASSIFIED_DEPLOYMENT_BARE_READS,
)

COMPOSE_FILE = pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml"
ANCHOR_KEY = "x-tai-app-env"


def main() -> int:
    doc = yaml.safe_load(COMPOSE_FILE.read_text())
    anchor = doc.get(ANCHOR_KEY)
    if not isinstance(anchor, dict):
        print(f"FAIL: {ANCHOR_KEY} anchor not found (renamed or restructured?)", file=sys.stderr)
        return 1
    raw = anchor.get(PINNED_KEYS_ENV)
    if not isinstance(raw, str):
        print(f"FAIL: {ANCHOR_KEY} does not declare {PINNED_KEYS_ENV}", file=sys.stderr)
        return 1
    declared_list = json.loads(raw)
    if not isinstance(declared_list, list) or not all(isinstance(key, str) and key for key in declared_list):
        print(f"FAIL: {PINNED_KEYS_ENV} is not a JSON list of env names: {raw!r}", file=sys.stderr)
        return 1

    pinned = set(anchor) - set(X_CLASSIFIED_DEPLOYMENT_BARE_READS)
    declared = set(declared_list)

    extra = pinned - declared  # in the anchor, not declared
    missing = declared - pinned  # declared, not in the anchor
    if extra or missing:
        print(f"FAIL: compose anchor parity mismatch vs {PINNED_KEYS_ENV}", file=sys.stderr)
        if extra:
            print(f"  in {ANCHOR_KEY} but NOT in {PINNED_KEYS_ENV}: {sorted(extra)}", file=sys.stderr)
        if missing:
            print(f"  in {PINNED_KEYS_ENV} but NOT in {ANCHOR_KEY}: {sorted(missing)}", file=sys.stderr)
        return 1

    print(f"OK: compose pinned-set parity — {len(declared)} anchor keys match {PINNED_KEYS_ENV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
