#!/usr/bin/env python3
"""
Maintains breaking_change.md from the "Breaking change migration" section of pull requests.

The functions are pure (text in, text out) so they can be unit tested. Only `main()` reads
the environment and touches files. Used by the GitHub workflows `breaking-change-log.yml`
(on merge), `gen_changelog.yml` (on release) and `pr-labels.yaml` (validation).
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

MIGRATION_HEADING = "Breaking change migration"
UNRELEASED_HEADING = "## Unreleased"
DOCUMENT_HEADER = (
    "# Breaking changes\n\n"
    "What users have to change when updating. Entries are added automatically when a pull request labelled "
    "`breaking-change` is merged, newest first, and grouped under the release that shipped them.\n"
)

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_ENTRY_HEADING = re.compile(r"^### #(\d+) ", re.MULTILINE)
_SECTION_HEADING = re.compile(r"^## ", re.MULTILINE)


def extract_migration(body: str) -> Optional[str]:
    """
    Return the text of the "Breaking change migration" section of a PR description.
    :param body: PR description (Markdown).
    :return: The section text, or None when the section is missing or empty (comments only).
    """
    match = re.search(rf"^##[ \t]+{re.escape(MIGRATION_HEADING)}[ \t]*\r?\n", body, re.MULTILINE | re.IGNORECASE)
    if match is None:
        return None
    rest = body[match.end() :]
    next_heading = _SECTION_HEADING.search(rest)
    section = rest[: next_heading.start()] if next_heading else rest
    section = _HTML_COMMENT.sub("", section).replace("\r\n", "\n").strip()
    return section or None


def format_entry(number: int, title: str, migration: str) -> str:
    """
    Format one document entry.
    :param number: PR number.
    :param title: PR title.
    :param migration: Migration text from extract_migration().
    """
    single_line_title = " ".join(title.split())
    return f"### #{number} {single_line_title}\n\n{migration.strip()}\n"


def add_entry(document: str, number: int, entry: str) -> str:
    """
    Insert an entry at the top of the "Unreleased" section, creating the section when needed.
    :param document: Current content of breaking_change.md (may be empty).
    :param number: PR number of the entry; a PR that is already listed leaves the document unchanged.
    :param entry: Entry text from format_entry().
    """
    if any(int(found) == number for found in _ENTRY_HEADING.findall(document)):
        return document

    if not document.strip():
        document = DOCUMENT_HEADER

    unreleased = re.search(rf"^{re.escape(UNRELEASED_HEADING)}[ \t]*\n", document, re.MULTILINE)
    if unreleased is not None:
        return document[: unreleased.end()] + "\n" + entry + _blank_line_before_rest(document[unreleased.end() :])

    first_release = _SECTION_HEADING.search(document)
    insert_at = first_release.start() if first_release else len(document)
    head = document[:insert_at].rstrip("\n") + "\n\n"
    return head + UNRELEASED_HEADING + "\n\n" + entry + _blank_line_before_rest(document[insert_at:])


def get_unreleased(document: str) -> Optional[str]:
    """
    Return the entries below the "Unreleased" heading.
    :param document: Content of breaking_change.md.
    :return: The entries, or None when there is no section or it is empty.
    """
    span = _unreleased_span(document)
    if span is None:
        return None
    entries = document[span[0] : span[1]].strip()
    return entries or None


def release_document(document: str, version: str, date: str) -> str:
    """
    Turn the "Unreleased" heading into a release heading. The document stays unchanged without unreleased entries.
    :param document: Current content of breaking_change.md.
    :param version: Release tag, for example v1.1.19.
    :param date: Release date (YYYY-MM-DD).
    """
    if get_unreleased(document) is None:
        return document
    return re.sub(rf"^{re.escape(UNRELEASED_HEADING)}[ \t]*$", f"## {version} ({date})", document, count=1, flags=re.MULTILINE)


def format_release_notes(version: str, entries: str) -> str:
    """
    Format the migration block that is appended to the GitHub release notes.
    :param version: Release tag.
    :param entries: Entries from get_unreleased().
    """
    demoted = re.sub(r"^### ", "#### ", entries, flags=re.MULTILINE)
    return f"## Breaking change migration\n\n{demoted}\n\nAll migration notes: [breaking_change.md](https://github.com/OHF-Voice/linux-voice-assistant/blob/{version}/breaking_change.md)\n"


def main(argv: Optional[list[str]] = None) -> int:
    """
    Command line entry point. The PR data is read from the environment (PR_NUMBER, PR_TITLE, PR_BODY), never from arguments.
    :param argv: Arguments, defaults to sys.argv.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Fail when PR_BODY has no migration text.")
    add = sub.add_parser("add", help="Add the PR to the Unreleased section.")
    add.add_argument("--file", type=Path, default=Path("breaking_change.md"))
    release = sub.add_parser("release", help="Close the Unreleased section and write the release notes block.")
    release.add_argument("--file", type=Path, default=Path("breaking_change.md"))
    release.add_argument("--version", required=True)
    release.add_argument("--date", required=True)
    release.add_argument("--notes-out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "check":
        if extract_migration(os.environ.get("PR_BODY", "")) is None:
            print(f"Breaking changes need a filled '{MIGRATION_HEADING}' section in the PR description.", file=sys.stderr)
            return 1
        return 0

    document = args.file.read_text(encoding="utf-8") if args.file.exists() else ""

    if args.command == "add":
        migration = extract_migration(os.environ.get("PR_BODY", ""))
        if migration is None:
            print(f"No '{MIGRATION_HEADING}' text in PR, nothing added.", file=sys.stderr)
            return 1
        entry = format_entry(int(os.environ["PR_NUMBER"]), os.environ.get("PR_TITLE", ""), migration)
        args.file.write_text(add_entry(document, int(os.environ["PR_NUMBER"]), entry), encoding="utf-8")
        return 0

    entries = get_unreleased(document)
    if entries is None:
        print("No unreleased breaking changes.", file=sys.stderr)
        return 0
    args.notes_out.write_text(format_release_notes(args.version, entries), encoding="utf-8")
    args.file.write_text(release_document(document, args.version, args.date), encoding="utf-8")
    return 0


def _unreleased_span(document: str) -> Optional[tuple[int, int]]:
    start = re.search(rf"^{re.escape(UNRELEASED_HEADING)}[ \t]*\n", document, re.MULTILINE)
    if start is None:
        return None
    next_heading = _SECTION_HEADING.search(document, start.end())
    return start.end(), next_heading.start() if next_heading else len(document)


def _blank_line_before_rest(rest: str) -> str:
    return "\n" + rest.lstrip("\n") if rest.strip() else ""


if __name__ == "__main__":
    sys.exit(main())
