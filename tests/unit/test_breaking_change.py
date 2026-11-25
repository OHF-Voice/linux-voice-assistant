"""Unit tests for .github/scripts/breaking_change.py (breaking_change.md maintenance)."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / ".github" / "scripts" / "breaking_change.py"
_spec = importlib.util.spec_from_file_location("breaking_change", _SCRIPT)
assert _spec is not None and _spec.loader is not None
breaking_change = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(breaking_change)

BODY = """# What does this implement/fix?

Something.

## Breaking change migration

<!-- Only for breaking changes. -->

Rename `FOO` to `BAR` in your `.env`.

## Behavior changes

- none
"""


def test_extract_migration_returns_section_text():
    assert breaking_change.extract_migration(BODY) == "Rename `FOO` to `BAR` in your `.env`."


def test_extract_migration_none_when_only_comment():
    body = "## Breaking change migration\n\n<!-- hint -->\n\n## Checklist\n"
    assert breaking_change.extract_migration(body) is None


def test_extract_migration_none_when_section_missing():
    assert breaking_change.extract_migration("## Behavior changes\n\ntext\n") is None


def test_extract_migration_runs_to_end_and_handles_crlf():
    body = "## Breaking change migration\r\n\r\nDo this.\r\nThen that.\r\n"
    assert breaking_change.extract_migration(body) == "Do this.\nThen that."


def test_format_entry_flattens_title_to_one_line():
    entry = breaking_change.format_entry(12, "Rename\nthe  option", "Do this.")
    assert entry == "### #12 Rename the option\n\nDo this.\n"


def test_add_entry_creates_document_with_unreleased_section():
    entry = breaking_change.format_entry(1, "First", "Text one.")
    result = breaking_change.add_entry("", 1, entry)
    assert result.startswith("# Breaking changes")
    assert "## Unreleased\n\n### #1 First\n\nText one.\n" in result


def test_add_entry_puts_newest_first_in_unreleased():
    document = breaking_change.add_entry("", 1, breaking_change.format_entry(1, "First", "One."))
    document = breaking_change.add_entry(document, 2, breaking_change.format_entry(2, "Second", "Two."))
    assert document.index("### #2 ") < document.index("### #1 ")
    assert document.count("## Unreleased") == 1


def test_add_entry_is_idempotent_per_pr_number():
    document = breaking_change.add_entry("", 1, breaking_change.format_entry(1, "First", "One."))
    assert breaking_change.add_entry(document, 1, breaking_change.format_entry(1, "First", "One.")) == document


def test_add_entry_creates_unreleased_above_existing_release():
    released = "# Breaking changes\n\nIntro.\n\n## v1.0.0 (2026-01-01)\n\n### #5 Old\n\nOld text.\n"
    result = breaking_change.add_entry(released, 6, breaking_change.format_entry(6, "New", "New text."))
    assert result.index("## Unreleased") < result.index("### #6 ") < result.index("## v1.0.0")
    assert "### #5 Old" in result


def test_get_unreleased_returns_only_unreleased_entries():
    document = "# T\n\n## Unreleased\n\n### #2 B\n\nTwo.\n\n## v1.0.0 (2026-01-01)\n\n### #1 A\n\nOne.\n"
    assert breaking_change.get_unreleased(document) == "### #2 B\n\nTwo."


def test_get_unreleased_none_without_section_or_entries():
    assert breaking_change.get_unreleased("# T\n\n## v1.0.0 (2026-01-01)\n") is None
    assert breaking_change.get_unreleased("# T\n\n## Unreleased\n\n## v1.0.0 (2026-01-01)\n") is None


def test_release_document_renames_unreleased_heading():
    document = "# T\n\n## Unreleased\n\n### #2 B\n\nTwo.\n\n## v1.0.0 (2026-01-01)\n"
    result = breaking_change.release_document(document, "v1.1.0", "2026-02-02")
    assert "## v1.1.0 (2026-02-02)" in result
    assert "## Unreleased" not in result


def test_release_document_unchanged_without_unreleased_entries():
    document = "# T\n\n## v1.0.0 (2026-01-01)\n\n### #1 A\n\nOne.\n"
    assert breaking_change.release_document(document, "v1.1.0", "2026-02-02") == document


def test_format_release_notes_demotes_headings_and_links_file():
    notes = breaking_change.format_release_notes("v1.1.0", "### #2 B\n\nTwo.")
    assert notes.startswith("## Breaking change migration\n\n#### #2 B\n\nTwo.")
    assert "blob/v1.1.0/breaking_change.md" in notes


def test_main_check_fails_without_migration(monkeypatch):
    monkeypatch.setenv("PR_BODY", "## Breaking change migration\n\n<!-- x -->\n")
    assert breaking_change.main(["check"]) == 1


def test_main_check_passes_with_migration(monkeypatch):
    monkeypatch.setenv("PR_BODY", BODY)
    assert breaking_change.main(["check"]) == 0


def test_main_add_writes_file(monkeypatch, tmp_path):
    target = tmp_path / "breaking_change.md"
    monkeypatch.setenv("PR_BODY", BODY)
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("PR_TITLE", "Rename FOO")
    assert breaking_change.main(["add", "--file", str(target)]) == 0
    assert "### #42 Rename FOO" in target.read_text(encoding="utf-8")


def test_main_add_fails_without_migration_and_writes_nothing(monkeypatch, tmp_path):
    target = tmp_path / "breaking_change.md"
    monkeypatch.setenv("PR_BODY", "no section")
    monkeypatch.setenv("PR_NUMBER", "42")
    assert breaking_change.main(["add", "--file", str(target)]) == 1
    assert not target.exists()


def test_main_release_writes_notes_and_closes_section(tmp_path):
    target = tmp_path / "breaking_change.md"
    notes = tmp_path / "notes.md"
    target.write_text("# T\n\n## Unreleased\n\n### #2 B\n\nTwo.\n", encoding="utf-8")
    args = ["release", "--file", str(target), "--version", "v1.1.0", "--date", "2026-02-02", "--notes-out", str(notes)]
    assert breaking_change.main(args) == 0
    assert "## v1.1.0 (2026-02-02)" in target.read_text(encoding="utf-8")
    assert "#### #2 B" in notes.read_text(encoding="utf-8")


def test_main_release_without_entries_writes_no_notes(tmp_path):
    target = tmp_path / "breaking_change.md"
    notes = tmp_path / "notes.md"
    args = ["release", "--file", str(target), "--version", "v1.1.0", "--date", "2026-02-02", "--notes-out", str(notes)]
    assert breaking_change.main(args) == 0
    assert not notes.exists()


def test_main_requires_command():
    with pytest.raises(SystemExit):
        breaking_change.main([])
