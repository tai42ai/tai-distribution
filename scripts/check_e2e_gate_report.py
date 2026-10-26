#!/usr/bin/env python3
"""Pass the e2e gate only when the suite ran tests and every one passed.

The e2e gate runs the tai42-e2e suite against the image in target mode, where
each test the target cannot serve is skipped with its unmet need as the reason.
pytest exits 0 when every collected test is skipped, so its exit status alone
would pass a run in which nothing ran. This reads the run's junit report instead
and requires at least one test that ran, with no failure and no error.

The counts (ran, skipped, failed, errors) and every failed or errored test are
printed, and written as a table to `--summary` (default: the file
`GITHUB_STEP_SUMMARY` names, when set).

Exit 0 pass, 1 a failure, an error, or no test ran, 2 an unreadable or malformed
report. Every non-happy path prints a `::error::` line; no silent fallback.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

_COUNTS = ("tests", "skipped", "failures", "errors")


class ReportError(Exception):
    """A report that cannot be read as a junit tally."""


@dataclass
class Tally:
    tests: int = 0
    skipped: int = 0
    failures: int = 0
    errors: int = 0
    broken: list[str] = field(default_factory=list)

    @property
    def ran(self) -> int:
        return self.tests - self.skipped - self.failures - self.errors


def _count(suite: ET.Element, name: str, path: Path) -> int:
    value = suite.get(name)
    if value is None:
        raise ReportError(f"{path}: a testsuite has no {name} count")
    try:
        return int(value)
    except ValueError as exc:
        raise ReportError(f"{path}: testsuite {name}={value!r} is not a count") from exc


def read_tally(path: Path) -> Tally:
    """Sum the counts of every testsuite in a junit report and name its broken cases."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ReportError(f"{path} is not a junit report: {exc}") from exc
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    if not suites:
        raise ReportError(f"{path}: the report holds no testsuite")
    tally = Tally()
    for suite in suites:
        for name in _COUNTS:
            setattr(tally, name, getattr(tally, name) + _count(suite, name, path))
        for case in suite.iter("testcase"):
            for outcome, label in (("failure", "failed"), ("error", "error")):
                for child in case.findall(outcome):
                    test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
                    tally.broken.append(f"{test_id} {label}: {child.get('message', '')}")
    return tally


def summary_text(tally: Tally) -> str:
    lines = [
        "### e2e gate",
        "",
        "| ran | skipped | failed | errors |",
        "| --- | --- | --- | --- |",
        f"| {tally.ran} | {tally.skipped} | {tally.failures} | {tally.errors} |",
        "",
    ]
    if tally.broken:
        lines += [f"- `{entry}`" for entry in tally.broken] + [""]
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    parser.add_argument(
        "--summary", type=Path, default=os.environ.get("GITHUB_STEP_SUMMARY") or None
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        tally = read_tally(args.junit)
    except (ReportError, OSError) as exc:
        print(f"::error::{exc}")
        return 2

    print(
        f"e2e gate: {tally.ran} ran, {tally.skipped} skipped, "
        f"{tally.failures} failed, {tally.errors} errors"
    )
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(summary_text(tally))
    for entry in tally.broken:
        print(f"::error::{entry}")
    if tally.failures or tally.errors:
        print(f"::error::the e2e suite failed against the image ({args.junit})")
        return 1
    if tally.ran <= 0:
        print(
            f"::error::no test ran against the image: every one of {tally.tests} "
            f"collected tests was skipped ({args.junit})"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
