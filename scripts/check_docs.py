#!/usr/bin/env python3
"""Check Markdown documentation for recurring drift patterns."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class Rule:
    """One documentation drift rule."""

    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Violation:
    """One matched documentation drift violation."""

    path: Path
    line_number: int
    rule: str
    match: str


_IGNORED_SPAN_PATTERN = re.compile(r'`[^`]*`|"[^"]*"')


def _strip_ignored_spans(line: str) -> str:
    """Remove inline examples that mention banned phrases as examples."""
    return _IGNORED_SPAN_PATTERN.sub("", line)


RULES: tuple[Rule, ...] = (
    Rule(
        "temporal narrative",
        re.compile(
            r"\b(?:no longer|anymore|previously|used to|"
            r"rather than (?:an? )?(?:old|manual|legacy)|"
            r"replaces? the old|now (?:runs?|uses?|is|has|can|supports?|requires?))\b",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "project-management reference",
        re.compile(r"\b(?:Phase|Task)\s+\d+\b", re.IGNORECASE),
    ),
    Rule(
        "bare IPv4 address",
        re.compile(
            r"(?<![\w.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
            r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\w.])"
        ),
    ),
    Rule(
        "volatile measurement",
        re.compile(
            r"(?:~\s*[\d,]{4,}\b|\b\d+(?:\.\d+)?\s*(?:KB|MB|GB|KiB|MiB|GiB)\b)",
            re.IGNORECASE,
        ),
    ),
)


def default_paths(root: Path) -> list[Path]:
    """Return the Markdown files checked by the command."""
    return [
        root / "README.md",
        root / "agents.md",
        *sorted((root / "docs").glob("*.md")),
    ]


def scan_text(path: Path, text: str, rules: Iterable[Rule] = RULES) -> list[Violation]:
    """Scan text and return every rule violation."""
    violations: list[Violation] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        scanned_line = _strip_ignored_spans(line)
        for rule in rules:
            violations.extend(
                Violation(
                    path=path,
                    line_number=line_number,
                    rule=rule.name,
                    match=match.group(0),
                )
                for match in rule.pattern.finditer(scanned_line)
            )
    return violations


def scan_paths(paths: Iterable[Path]) -> list[Violation]:
    """Scan existing paths and return every violation."""
    violations: list[Violation] = []
    for path in paths:
        if path.exists():
            violations.extend(scan_text(path, path.read_text()))
    return violations


def main() -> int:
    """Run the documentation drift checker."""
    root = Path(__file__).resolve().parent.parent
    violations = scan_paths(default_paths(root))
    for violation in violations:
        rel_path = violation.path.relative_to(root)
        print(
            f"{rel_path}:{violation.line_number}: {violation.rule} — {violation.match}"
        )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
