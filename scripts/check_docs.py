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
_LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "ftp://", "irc://")


def _strip_ignored_spans(line: str) -> str:
    """Remove inline examples that mention banned phrases as examples."""
    return _IGNORED_SPAN_PATTERN.sub("", line)


def heading_to_slug(heading: str) -> str:
    """Convert a markdown heading into a GitHub-compatible anchor slug."""
    cleaned = re.sub(r"[`*_~]", "", heading)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[^\w\s-]", "", cleaned).strip().lower()
    return re.sub(r"\s+", "-", cleaned)


def extract_slugs_and_anchors(text: str) -> set[str]:
    """Extract heading slugs and HTML anchor names/ids from markdown text."""
    slugs: set[str] = set()
    for line in text.splitlines():
        heading_match = re.match(r"^#{1,6}\s+(.*)$", line.strip())
        if heading_match:
            slug = heading_to_slug(heading_match.group(1))
            if slug:
                slugs.add(slug)
    for html_match in re.finditer(r'<a\s+[^>]*(?:id|name)=["\']([^"\']+)["\']', text):
        slugs.add(html_match.group(1))
    return slugs


def scan_links(path: Path, text: str) -> list[Violation]:
    """Scan markdown text for broken relative file links and anchor tags."""
    violations: list[Violation] = []
    in_code_block = False

    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        for match in _LINK_PATTERN.finditer(line):
            target = match.group(2).strip()
            if target.startswith(_EXTERNAL_SCHEMES):
                continue

            target_path_str, _, anchor = target.partition("#")

            if target_path_str:
                target_path = (path.parent / target_path_str).resolve()
                if not target_path.exists():
                    violations.append(
                        Violation(
                            path=path,
                            line_number=line_number,
                            rule="broken link",
                            match=target,
                        )
                    )
                    continue
                if anchor and target_path.suffix == ".md":
                    target_text = target_path.read_text()
                    valid_slugs = extract_slugs_and_anchors(target_text)
                    if anchor not in valid_slugs:
                        violations.append(
                            Violation(
                                path=path,
                                line_number=line_number,
                                rule="broken anchor",
                                match=target,
                            )
                        )
            elif anchor:
                valid_slugs = extract_slugs_and_anchors(text)
                if anchor not in valid_slugs:
                    violations.append(
                        Violation(
                            path=path,
                            line_number=line_number,
                            rule="broken anchor",
                            match=target,
                        )
                    )

    return violations


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
            text = path.read_text()
            violations.extend(scan_text(path, text))
            violations.extend(scan_links(path, text))
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
