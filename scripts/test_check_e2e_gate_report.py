"""Tests for check_e2e_gate_report: synthetic junit reports only."""

from __future__ import annotations

from pathlib import Path

import check_e2e_gate_report as report
import pytest


def _case(name: str, outcome: str = "passed", message: str = "") -> str:
    body = {
        "passed": "",
        "skipped": f'<skipped type="pytest.skip" message="{message}"/>',
        "failed": f'<failure message="{message}">trace</failure>',
        "error": f'<error message="{message}">trace</error>',
    }[outcome]
    return f'<testcase classname="tests.test_mod" name="{name}" time="0.1">{body}</testcase>'


def _junit(tmp_path: Path, cases: list[str], *, wrap: bool = True, **counts) -> Path:
    """A pytest-shaped junit file; the suite attributes default to the case tally."""
    tally = {
        "tests": len(cases),
        "skipped": sum("<skipped" in c for c in cases),
        "failures": sum("<failure" in c for c in cases),
        "errors": sum("<error" in c for c in cases),
    }
    tally.update(counts)
    attrs = " ".join(f'{k}="{v}"' for k, v in tally.items())
    suite = f'<testsuite name="pytest" {attrs}>{"".join(cases)}</testsuite>'
    text = f"<testsuites>{suite}</testsuites>" if wrap else suite
    path = tmp_path / "junit.xml"
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?>{text}')
    return path


# --- tally --------------------------------------------------------------------


def test_tally_counts_ran_as_tests_minus_skipped_failures_errors(tmp_path):
    path = _junit(
        tmp_path,
        [_case("a"), _case("b"), _case("c", "skipped", "target reports kind x off")],
    )
    tally = report.read_tally(path)
    assert (tally.tests, tally.ran, tally.skipped) == (3, 2, 1)
    assert (tally.failures, tally.errors) == (0, 0)


def test_tally_reads_a_bare_testsuite_root(tmp_path):
    path = _junit(tmp_path, [_case("a")], wrap=False)
    assert report.read_tally(path).ran == 1


def test_tally_names_each_failed_and_errored_case(tmp_path):
    path = _junit(
        tmp_path,
        [_case("a"), _case("b", "failed", "assert 1 == 2"), _case("c", "error", "boom")],
    )
    tally = report.read_tally(path)
    assert tally.broken == [
        "tests.test_mod::b failed: assert 1 == 2",
        "tests.test_mod::c error: boom",
    ]


def test_tally_rejects_malformed_xml(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text("<testsuites><testsuite")
    with pytest.raises(report.ReportError, match="not a junit report"):
        report.read_tally(path)


def test_tally_rejects_a_report_without_a_testsuite(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text("<testsuites/>")
    with pytest.raises(report.ReportError, match="no testsuite"):
        report.read_tally(path)


def test_tally_rejects_a_suite_missing_a_count(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text('<testsuite name="pytest" tests="1" failures="0" errors="0"/>')
    with pytest.raises(report.ReportError, match="skipped"):
        report.read_tally(path)


# --- CLI end to end -----------------------------------------------------------


def test_main_some_ran_and_passed_exit_0(tmp_path, capsys):
    path = _junit(tmp_path, [_case("a"), _case("b", "skipped", "built-stack only")])
    summary = tmp_path / "summary.md"
    assert report.main([str(path), "--summary", str(summary)]) == 0
    out = capsys.readouterr().out
    assert "1 ran, 1 skipped, 0 failed, 0 errors" in out
    assert "::error::" not in out
    text = summary.read_text()
    assert "| 1 | 1 | 0 | 0 |" in text


def test_main_all_skipped_exit_1(tmp_path, capsys):
    path = _junit(tmp_path, [_case("a", "skipped"), _case("b", "skipped")])
    assert report.main([str(path)]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out and "no test ran" in out


def test_main_empty_run_exit_1(tmp_path, capsys):
    path = _junit(tmp_path, [])
    assert report.main([str(path)]) == 1
    assert "no test ran" in capsys.readouterr().out


def test_main_failures_exit_1_naming_each_case(tmp_path, capsys):
    path = _junit(tmp_path, [_case("a"), _case("b", "failed", "assert 1 == 2")])
    summary = tmp_path / "summary.md"
    assert report.main([str(path), "--summary", str(summary)]) == 1
    out = capsys.readouterr().out
    assert "::error::tests.test_mod::b failed: assert 1 == 2" in out
    assert "tests.test_mod::b" in summary.read_text()


def test_main_errors_exit_1(tmp_path, capsys):
    path = _junit(tmp_path, [_case("a"), _case("b", "error", "fixture broke")])
    assert report.main([str(path)]) == 1
    assert "::error::tests.test_mod::b error: fixture broke" in capsys.readouterr().out


def test_main_malformed_xml_exit_2(tmp_path, capsys):
    path = tmp_path / "junit.xml"
    path.write_text("not xml")
    assert report.main([str(path)]) == 2
    assert "::error::" in capsys.readouterr().out


def test_main_missing_report_exit_2(tmp_path, capsys):
    assert report.main([str(tmp_path / "absent.xml")]) == 2
    assert "::error::" in capsys.readouterr().out


def test_main_writes_no_summary_when_none_is_named(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    path = _junit(tmp_path, [_case("a")])
    assert report.main([str(path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["junit.xml"]


def test_main_summary_defaults_to_the_step_summary_file(tmp_path, monkeypatch):
    summary = tmp_path / "step-summary.md"
    summary.write_text("earlier step\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    path = _junit(tmp_path, [_case("a")])
    assert report.main([str(path)]) == 0
    text = summary.read_text()
    assert text.startswith("earlier step\n")
    assert "| 1 | 0 | 0 | 0 |" in text
