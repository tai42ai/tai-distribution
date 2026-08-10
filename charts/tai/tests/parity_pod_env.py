#!/usr/bin/env python3
"""k8s tier-2 refusal parity: the chart's pinned pod-env keys vs the product.

Renders the FULL pinned pod env of BOTH app deployments (serve + backend) with
every conditional feature ON, then asserts — against the single source of truth
``tai42_skeleton.config.recycle_policy`` — that the recycle-pinned subset of that
env is EXACTLY ``TIER2_K8S_REFUSED_KEYS``. A store/connection key added to the
chart that the product does not refuse (or a refused key the chart never pins)
fails here, so the k8s refusal list can never silently desync from the manifests.

The recycle-pinned subset is the rendered pod-env MINUS the keys refused on other
axes: the Tier-1 bus URLs (refused on every shape) and the X-band identity /
deployment-infra bare reads (refused at every env writer, never recyclable). The
X-band split is deliberate: the product's ``X_CLASSIFIED_DEPLOYMENT_BARE_READS``
covers the two deployment-infra bare reads (shape marker + sentinel path); the
config-mode / k8s-identity / plugin-prefix / multiproc bare reads are the chart's own
X-band keys, enumerated in ``CHART_X_BAND_KEYS`` below (a chart-side fact, not a
recycle-policy export).

Run: python charts/tai/tests/parity_pod_env.py   (needs helm on PATH and
tai42-skeleton importable — see the CI job).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import yaml

from tai42_skeleton.config.recycle_policy import (
    TIER1_REFUSED_KEYS,
    TIER2_K8S_REFUSED_KEYS,
    X_CLASSIFIED_DEPLOYMENT_BARE_READS,
    Shape,
    refused_keys,
)

CHART_DIR = pathlib.Path(__file__).resolve().parent.parent
VALUES = CHART_DIR / "tests" / "all-features-values.yaml"

# The chart's own X-band pod-env keys under k8s config mode: identity/config
# bare reads that no recycle path reaches (carried untouched across a profile
# apply), so they are excluded from the recycle-pinned comparison just like the
# Tier-1 bus URL. Membership here is the discriminator between "pinned but
# X-refused" and "pinned and Tier-2-refused".
#
# INVARIANT: this hand-maintained frozenset holds ONLY non-recycle-class
# bootstrap/identity keys (config mode, k8s object names, plugins prefix, multiproc
# dir) — the manual escape hatch for keys that are pinned but legitimately not
# profile-expressible. A connection/recycle-class key must NEVER be added here; it
# belongs in the product refusal list. main() asserts disjointness from
# TIER2_K8S_REFUSED_KEYS and fails loud if the two ever overlap.
CHART_X_BAND_KEYS: frozenset[str] = frozenset(
    {
        "TAI_CONFIG_MODE",
        "TAI_K8S_SECRET_NAME",
        "TAI_K8S_CONFIGMAP_NAME",
        "TAI_K8S_MANIFEST_KEY",
        "TAI_PLUGINS_PREFIX",
        "PROMETHEUS_MULTIPROC_DIR",
    }
) | X_CLASSIFIED_DEPLOYMENT_BARE_READS

# The app container (a recycle target) in each app deployment. The metrics
# sidecar and the dependency-wait initContainer are NOT recycle targets, so their
# env is excluded — only these named main containers are scanned.
APP_CONTAINERS = {"serve", "backend"}


def _render(backend_type: str) -> str:
    return subprocess.run(
        [
            "helm",
            "template",
            "tai",
            str(CHART_DIR),
            "-f",
            str(VALUES),
            "--set",
            f"backend.type={backend_type}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _pinned_env_keys(rendered: str) -> set[str]:
    keys: set[str] = set()
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "Deployment":
            continue
        containers = doc["spec"]["template"]["spec"].get("containers", [])
        for container in containers:
            if container.get("name") not in APP_CONTAINERS:
                continue
            for entry in container.get("env", []):
                keys.add(entry["name"])
    return keys


def main() -> int:
    # A key can't be both an X-band escape and a Tier-2 refused key. Catch a
    # misplaced entry before rendering anything.
    both = CHART_X_BAND_KEYS & TIER2_K8S_REFUSED_KEYS
    if both:
        print(
            f"FAIL: CHART_X_BAND_KEYS overlaps TIER2_K8S_REFUSED_KEYS "
            f"(a key is either an X-band escape or Tier-2 refused, never both): {sorted(both)}",
            file=sys.stderr,
        )
        return 1

    rendered = set()
    for backend_type in ("arq", "celery"):
        rendered |= _pinned_env_keys(_render(backend_type))

    if not rendered:
        print("FAIL: no app-container env rendered (chart or container names changed?)", file=sys.stderr)
        return 1

    k8s_refused = refused_keys(Shape.k8s)  # Tier-1 ∪ Tier-2(k8s)
    allowed = k8s_refused | CHART_X_BAND_KEYS

    # 1) No pinned key escapes classification.
    unclassified = rendered - allowed
    # 2) The recycle-pinned subset (strip Tier-1 + X-band) is EXACTLY Tier-2(k8s):
    #    bidirectional, so a missing OR an extra key both fail.
    recycle_pinned = rendered - TIER1_REFUSED_KEYS - CHART_X_BAND_KEYS
    missing = TIER2_K8S_REFUSED_KEYS - recycle_pinned  # refused but never pinned
    extra = recycle_pinned - TIER2_K8S_REFUSED_KEYS  # pinned but not refused

    if unclassified or missing or extra:
        print("FAIL: k8s tier-2 pod-env parity mismatch vs recycle_policy", file=sys.stderr)
        if unclassified:
            print(f"  pinned but UNCLASSIFIED (not Tier-1/Tier-2/X-band): {sorted(unclassified)}", file=sys.stderr)
        if extra:
            print(f"  pinned + recycle-class but NOT in TIER2_K8S_REFUSED_KEYS: {sorted(extra)}", file=sys.stderr)
        if missing:
            print(f"  in TIER2_K8S_REFUSED_KEYS but NOT pinned by the chart: {sorted(missing)}", file=sys.stderr)
        return 1

    print(f"OK: k8s tier-2 parity — {len(TIER2_K8S_REFUSED_KEYS)} pinned recycle keys match recycle_policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
