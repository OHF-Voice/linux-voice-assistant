"""Turn a pytest JUnit XML file into a GitHub Actions job summary and error annotations.

GitHub Actions has no built-in test report. The job summary shows a table of all tests on the run page and an
annotation is created for every failed test, so a failure is visible without opening the log.

Usage: junit_summary.py <junit.xml> [<title>]
The summary is appended to the file named by GITHUB_STEP_SUMMARY, or printed when the variable is not set.
"""

import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List


#: A longer list of tests is collapsed in the summary, failures are always listed below it
_COLLAPSE_ABOVE = 50

#: Limits of the failure details, so one failure cannot flood the summary
_MAX_DETAIL = 3000
_MAX_LINE = 300


@dataclass(frozen=True)
class TestResult:
    name: str
    path: str
    seconds: float
    outcome: str
    detail: str
    description: str = ""


def main() -> int:
    results = _read_results(sys.argv[1])
    title = sys.argv[2] if len(sys.argv) > 2 else "Test results"
    summary = _render_summary(title, results)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(summary)
    else:
        print(summary)

    for result in results:
        if result.outcome == "failed":
            print(f"::error file={result.path},title={_escape(result.name, True)}::{_escape(result.detail.splitlines()[0] if result.detail else 'failed', False)}")
    return 0


def _read_results(junit_path: str) -> List[TestResult]:
    results = []
    for case in ET.parse(junit_path).getroot().iter("testcase"):
        outcome, detail = "passed", ""
        for child in case:
            if child.tag in ("failure", "error"):
                outcome, detail = "failed", (child.get("message") or "") + "\n" + (child.text or "")
            elif child.tag == "skipped":
                outcome, detail = "skipped", child.get("message") or ""
        module = (case.get("classname") or "").replace(".", "/")
        description = next((prop.get("value") or "" for prop in case.iter("property") if prop.get("name") == "description"), "")
        results.append(TestResult(case.get("name") or "", f"{module}.py", float(case.get("time") or 0), outcome, detail.strip(), description))
    return results


def _render_summary(title: str, results: List[TestResult]) -> str:
    failed = [result for result in results if result.outcome == "failed"]
    skipped = [result for result in results if result.outcome == "skipped"]
    passed = len(results) - len(failed) - len(skipped)
    icon = {"passed": "✅", "failed": "❌", "skipped": "⏭️"}

    lines = [f"## {title}", "", f"{passed} passed, {len(failed)} failed, {len(skipped)} skipped in {sum(r.seconds for r in results):.0f} s", ""]
    with_description = any(r.description for r in results)
    table = ["| | Test | Description | Time |", "|---|---|---|---|"] if with_description else ["| | Test | Time |", "|---|---|---|"]
    for r in results:
        cells = [icon[r.outcome], f"`{r.path}::{r.name}`", *([r.description.replace("|", "\\|")] if with_description else []), f"{r.seconds:.1f} s"]
        table.append("| " + " | ".join(cells) + " |")
    if len(results) > _COLLAPSE_ABOVE:
        lines += [f"<details><summary>All {len(results)} tests</summary>", ""] + table + ["", "</details>"]
    else:
        lines += table
    for result in failed:
        lines += ["", f"<details><summary>❌ {result.name}</summary>", "", _fenced(_short_detail(result.detail)), "</details>"]
    return "\n".join(lines) + "\n"


def _short_detail(detail: str) -> str:
    """The error lines of a failure (pytest marks them with ``E``), or the start of the report if there are none. Long lines are cut."""
    lines = detail.splitlines()
    error_lines = [line for line in lines if line.startswith("E ")]
    shown = error_lines or lines
    return "\n".join(line if len(line) <= _MAX_LINE else line[:_MAX_LINE] + " ..." for line in shown)[:_MAX_DETAIL]


def _fenced(text: str) -> str:
    """A code block around the text that stays closed even if the text contains a code fence."""
    fence = "```" if "```" not in text else "~~~~"
    return f"{fence}\n{text}\n{fence}"


def _escape(text: str, is_property: bool) -> str:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if is_property else text


if __name__ == "__main__":
    sys.exit(main())
