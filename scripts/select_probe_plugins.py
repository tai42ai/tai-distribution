#!/usr/bin/env python3
"""Derive the constraint-probe plugin versions from the image's pinned core.

The release image sets PIP_CONSTRAINT/UV_CONSTRAINT to a constraints file of its
own first-party core versions, so a plugin that needs a different core major is
refused by the resolver instead of silently downgrading the core. CI proves that
with two probes against a real marketplace plugin: one version the constraint
must ADMIT (its tai42-contract and tai42-kit requirements contain the image's
pinned versions) and one older version the constraint must REFUSE (its
requirements exclude them). Those two versions move every time the core majors
bump, so they are derived here from the manifest's pinned core and the plugin's
published metadata instead of hand-kept in the workflow.

Reads the tai42-contract and tai42-kit `==` pins from the image manifest
(docker/pypi-requirements.txt), queries the plugin's PyPI JSON for every
non-yanked release's tai42-contract and tai42-kit requirements, and prints two
GITHUB_OUTPUT-shaped lines the workflow consumes directly:

    accept=<newest release whose requirements admit the pinned core>
    refuse=<newest older release whose requirements do not admit it>

The refusal version is the newest release OLDER than the accept version whose
requirements exclude the pinned core — a plugin built for a previous core
generation, which the constraint must reject. A newer release that pins to a
future core is not a previous-generation refusal and is never chosen.

Every failure raises and exits non-zero: a manifest missing either core pin, a
plugin with no non-yanked release admitting the pinned core (nothing compatible
is published yet), or no older release the constraint would refuse. Nothing is
skipped silently.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

import derive_constraints

_PYPI = "https://pypi.org/pypi"
_CONTRACT = "tai42-contract"
_KIT = "tai42-kit"
_CORE = (_CONTRACT, _KIT)
_DEFAULT_PLUGIN = "tai42-monitoring-langfuse"


class ProbeError(Exception):
    """A condition that cannot yield the two probe versions."""


def _normalize(name: str) -> str:
    """PEP 503 name normalization, so a requirement matches regardless of case."""
    return re.sub(r"[-_.]+", "-", name).lower()


def core_pins(manifest_text: str) -> tuple[str, str]:
    """Return the (tai42-contract, tai42-kit) `==` pins from the image manifest."""
    pins: dict[str, str] = {}
    for line in derive_constraints.parse_manifest(manifest_text):
        name, version = line.split("==", 1)
        pins[name] = version
    missing = [name for name in _CORE if name not in pins]
    if missing:
        raise ProbeError(
            f"manifest is missing a required core pin: {', '.join(missing)}"
        )
    return pins[_CONTRACT], pins[_KIT]


def _core_specifiers(requires_dist: list[str] | None) -> dict[str, SpecifierSet]:
    """The tai42-contract/tai42-kit requirement specifiers a release declares.

    Only unconditional requirements count: a core requirement gated behind an
    environment marker (e.g. an unrequested extra) is not applied by a plain
    `pip install <plugin>==<version>`, so it does not shape the probe.
    """
    specifiers: dict[str, SpecifierSet] = {}
    for raw in requires_dist or []:
        req = Requirement(raw)
        name = _normalize(req.name)
        if name in _CORE and req.marker is None:
            specifiers[name] = specifiers.get(name, SpecifierSet()) & req.specifier
    return specifiers


def admits(
    requires_dist: list[str] | None, pinned_contract: str, pinned_kit: str
) -> bool:
    """Whether a release's core requirements contain the pinned core versions."""
    specifiers = _core_specifiers(requires_dist)
    contract = specifiers.get(_CONTRACT)
    kit = specifiers.get(_KIT)
    if contract is not None and not contract.contains(pinned_contract, prereleases=True):
        return False
    if kit is not None and not kit.contains(pinned_kit, prereleases=True):
        return False
    return True


def select_probe_versions(
    catalog: dict[str, dict],
    pinned_contract: str,
    pinned_kit: str,
    plugin: str,
) -> tuple[str, str]:
    """Choose the accept and refuse probe versions from a release catalog.

    `catalog` maps each release's version string to a dict with `requires_dist`
    (the list of requirement strings) and `yanked` (bool). A release is not a
    probe candidate when it is yanked, its version is not PEP 440, it is a
    pre-release (the probes install what an operator gets by default), or its
    `requires_dist` is null — null is unknown metadata, not a declaration of no
    core dependency, so its compatibility cannot be trusted either way. An empty
    requirement list, by contrast, honestly declares no core dependency.
    """
    admitting: list[tuple[Version, str]] = []
    refusing: list[tuple[Version, str]] = []
    for version_str, info in catalog.items():
        if info.get("yanked"):
            continue
        requires_dist = info.get("requires_dist")
        if requires_dist is None:
            continue
        try:
            version = Version(version_str)
        except InvalidVersion:
            continue
        if version.is_prerelease:
            continue
        target = admitting if admits(requires_dist, pinned_contract, pinned_kit) else refusing
        target.append((version, version_str))
    if not admitting:
        raise ProbeError(
            f"no non-yanked {plugin} release admits the pinned core "
            f"({_CONTRACT}=={pinned_contract}, {_KIT}=={pinned_kit}); "
            "no compatible plugin is published yet"
        )
    accept_version, accept_str = max(admitting)
    older_refusing = [(v, s) for v, s in refusing if v < accept_version]
    if not older_refusing:
        raise ProbeError(
            f"no {plugin} release older than {accept_str} excludes the pinned core "
            f"({_CONTRACT}=={pinned_contract}, {_KIT}=={pinned_kit}); "
            "the constraint has no previous-generation plugin to refuse"
        )
    _, refuse_str = max(older_refusing)
    return accept_str, refuse_str


def _get_json(url: str, opener: Callable) -> dict:
    try:
        with opener(url, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"could not fetch {url}: {exc}") from exc


def fetch_catalog(
    plugin: str, opener: Callable = urllib.request.urlopen
) -> dict[str, dict]:
    """Build the release catalog for a plugin from PyPI's JSON API.

    The project JSON lists every release; the per-release JSON carries that
    release's `requires_dist` and `yanked` flag (the project JSON only carries
    the latest release's metadata).
    """
    index = _get_json(f"{_PYPI}/{plugin}/json", opener)
    catalog: dict[str, dict] = {}
    for version in index.get("releases", {}):
        info = _get_json(f"{_PYPI}/{plugin}/{version}/json", opener).get("info", {})
        catalog[version] = {
            "requires_dist": info.get("requires_dist"),
            "yanked": bool(info.get("yanked")),
        }
    if not catalog:
        raise ProbeError(f"{plugin} has no releases on PyPI")
    return catalog


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        required=True,
        help="the image manifest carrying the tai42-contract/tai42-kit == pins",
    )
    parser.add_argument(
        "--plugin",
        default=_DEFAULT_PLUGIN,
        help="the marketplace plugin the probes install (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest_text = Path(args.manifest).read_text(encoding="utf-8")
        pinned_contract, pinned_kit = core_pins(manifest_text)
        catalog = fetch_catalog(args.plugin)
        accept, refuse = select_probe_versions(
            catalog, pinned_contract, pinned_kit, args.plugin
        )
    except (ProbeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"accept={accept}")
    print(f"refuse={refuse}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
