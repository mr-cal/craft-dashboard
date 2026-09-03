"""Round-1 mandatory baseline gathering for open-issue/PR evaluation."""

from __future__ import annotations

import json
import logging
import re

from craft_dashboard.git_mirrors import reader
from craft_dashboard.git_mirrors.paths import mirror_path_for
from craft_dashboard.llm.tool_dispatch import ToolContext, _dispatch_http_tool

logger = logging.getLogger(__name__)

_MIN_USEFUL_HITS = 1
_MAX_USEFUL_HITS = 100
_MAX_CANDIDATE_PATTERNS = 10
_MIN_PATTERN_LENGTH = 4

_BACKTICK_RE = re.compile(r"`([^`]+)`")
_IDENTIFIER_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:_[A-Za-z0-9_]+)+|[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b"
)


class BaselineError(RuntimeError):
    """A mandatory baseline component failed after preflight passed."""


def _extract_candidate_patterns(text: str) -> list[str]:
    """Extract likely repo-search identifiers from issue/PR text."""
    candidates: list[str] = []
    for match in _BACKTICK_RE.finditer(text):
        candidate = match.group(1).strip().rstrip("()")
        if candidate:
            candidates.append(candidate)
    candidates.extend(match.group(1) for match in _IDENTIFIER_RE.finditer(text))

    seen: set[str] = set()
    patterns: list[str] = []
    for candidate in candidates:
        if len(candidate) < _MIN_PATTERN_LENGTH or candidate in seen:
            continue
        seen.add(candidate)
        patterns.append(candidate)

    patterns.sort(key=len, reverse=True)
    return patterns[:_MAX_CANDIDATE_PATTERNS]


async def build_round1_baseline(
    ctx: ToolContext, *, project: str, title: str, body: str | None
) -> str:
    """Build the mandatory repo/context baseline injected before tool rounds."""
    ref = ctx.pinned_shas.get(project)
    if ref is None:
        raise BaselineError(f"missing pinned SHA for project {project!r}")

    mirror = mirror_path_for(
        project, mirror_dir=ctx.mirror_dir, allowed_projects=ctx.allowed_projects
    )

    try:
        layout = await reader.repo_layout(mirror, ref=ref)
    except Exception as exc:
        raise BaselineError(
            f"repo_layout failed for {project}@{ref[:12]} after preflight"
        ) from exc

    sections: list[str] = [
        "## Repo layout\n"
        + (
            "\n".join(
                f"{directory}\t{count} files"
                for directory, count in sorted(layout.items())
            )
            or "(empty repository)"
        )
    ]

    grep_lines: list[str] = []
    patterns = _extract_candidate_patterns(f"{title}\n{body or ''}")
    grep_errors = 0
    for pattern in patterns:
        try:
            hits = await reader.grep_repo(mirror, pattern=pattern, ref=ref)
        except Exception:  # noqa: BLE001
            grep_errors += 1
            continue
        hit_count = len(hits)
        if _MIN_USEFUL_HITS <= hit_count <= _MAX_USEFUL_HITS:
            grep_lines.append(f"{pattern!r}: {hit_count} hit(s)")
    if patterns and grep_errors == len(patterns):
        raise BaselineError(
            f"grep_repo failed for every pattern on {project}@{ref[:12]}"
        )
    if grep_lines:
        sections.append(
            "## Candidate identifiers found in this repo\n" + "\n".join(grep_lines)
        )

    try:
        raw = await _dispatch_http_tool(
            ctx, name="related_issues", arguments={"query": title}
        )
        data = json.loads(raw)
        related = data.get("results", []) if isinstance(data, dict) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("related_issues endpoint failed during baseline: %s", exc)
        related = []

    if related:
        related_lines = [
            f"- {entry.get('project_name')}#{entry.get('external_id')}: {entry.get('title')}"
            for entry in related
        ]
        sections.append("## Related issues\n" + "\n".join(related_lines))

    return "\n\n".join(sections)
