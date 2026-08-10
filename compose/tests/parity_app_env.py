#!/usr/bin/env python3
"""compose tier-2 refusal parity: the ``x-tai-app-env`` anchor vs the product.

Asserts — against the single source of truth
``tai42_skeleton.config.recycle_policy`` — that the anchor's pinned keys are
EXACTLY ``TIER2_COMPOSE_REFUSED_KEYS``, so an anchor edit that is not mirrored in
the product's compose refusal list (or vice versa) fails CI. Every app service
reuses this one anchor, so its key set IS the compose deployment-value pinning.

The deployment-infra bare reads (``X_CLASSIFIED_DEPLOYMENT_BARE_READS`` — the
shape marker + sentinel path) are refused on the X axis at every env writer, not
on the compose Tier-2 axis, so they are excluded from both sides of the compare —
the same split the k8s parity test makes for its X-band keys.

Run: python compose/tests/parity_app_env.py   (needs tai42-skeleton importable —
see the CI job).
"""

from __future__ import annotations

import pathlib
import sys

import yaml

from tai42_skeleton.config.recycle_policy import (
    TIER2_COMPOSE_REFUSED_KEYS,
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

    x_band = set(X_CLASSIFIED_DEPLOYMENT_BARE_READS)
    pinned = set(anchor) - x_band  # recycle-pinned subset
    refused = set(TIER2_COMPOSE_REFUSED_KEYS)

    extra = pinned - refused  # in the anchor, not refused
    missing = refused - pinned  # refused, not in the anchor
    if extra or missing:
        print("FAIL: compose tier-2 anchor parity mismatch vs recycle_policy", file=sys.stderr)
        if extra:
            print(f"  in {ANCHOR_KEY} but NOT in TIER2_COMPOSE_REFUSED_KEYS: {sorted(extra)}", file=sys.stderr)
        if missing:
            print(f"  in TIER2_COMPOSE_REFUSED_KEYS but NOT in {ANCHOR_KEY}: {sorted(missing)}", file=sys.stderr)
        return 1

    print(f"OK: compose tier-2 parity — {len(refused)} anchor keys match recycle_policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
