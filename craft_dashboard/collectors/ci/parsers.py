"""Pure parsing functions for CI failure signatures.

Each parser takes raw text (a log tail or annotation message) and returns
zero or more ``ParsedFailure`` records. These are pure functions deliberately
(no network, no DB) so they can be tested directly against recorded sample
output without mocking a collector.

Signature normalization matches ``plans/44-ci-failure-catalog.md``'s Design
Decisions: Spread signatures drop the trailing run-id suffix, pytest/Go
signatures are kept as the stable ``path::test`` / ``package.Test``
identifiers GitHub/the tooling already emits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ParsedFailure",
    "detect_infra_error",
    "has_structured_parser_hint",
    "parse_check_annotations",
    "parse_go_failures",
    "parse_linter_and_doc_failures",
    "parse_log_tail",
    "parse_pytest_failures",
    "parse_spread_failures",
]


@dataclass(frozen=True, slots=True)
class ParsedFailure:
    """A single extracted test/tool failure signature."""

    test_type: str  # "pytest" | "spread" | "go" | "linter" | "infra"
    signature: str
    error_message: str | None = None
    is_environmental: bool = False


# GitHub's job-log API prefixes every raw line with the time it was
# written, e.g. "2026-10-28T19:45:13.0019151Z FAILED tests/foo.py::bar".
# Every other parser below anchors its pattern at the start of the line
# (pytest's ``FAILED``, Go's ``--- FAIL:``, ruff's ``path.py:LINE:COL:``),
# so without stripping this prefix first, none of them ever match a real
# production log tail -- and any fallback that keeps the raw line as its
# signature would embed a timestamp that never repeats, defeating
# deduplication across runs.
_GHA_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ?")


def _strip_gha_timestamps(log_text: str) -> str:
    """Remove GitHub Actions' leading per-line timestamp, if present."""
    return "\n".join(_GHA_TIMESTAMP_RE.sub("", line) for line in log_text.splitlines())


# Patterns that indicate the failure is environmental (runner/network/LXD)
# rather than a real test assertion. Checked against the extracted error
# message so any parser can tag a failure as `is_environmental`, not just
# the Spread parser.
_INFRA_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"LXDError",
        r"forkstart",
        r"failed to start container",
        r"Network is unreachable",
        r"Errno 101",
        r"Errno 104",
        r"Connection reset by peer",
        r"Temporary failure in name resolution",
        r"context deadline exceeded",
        r"no space left on device",
        r"disk quota exceeded",
        r"runner has received a shutdown signal",
        r"The runner has been deprovisioned",
        # GitHub Actions' own step/job-level cancellation and timeout
        # messages -- the most common "failure" text in production by far
        # (per-job annotations are GitHub's generic marker, never the
        # underlying tool's own output; see parse_check_annotations). These
        # mean a run was aborted by CI infrastructure or policy (a sibling
        # matrix cell failing, a newer push superseding it, a stuck step
        # hitting its timeout), not that the job's own code/tests failed.
        r"has timed out after",
        r"The operation was canceled",
        r"strategy configuration was canceled because",
        r"Canceling since a higher priority waiting request",
    )
)


def detect_infra_error(message: str) -> bool:
    """Return whether a failure message looks environmental, not a real test failure."""
    return any(pattern.search(message) for pattern in _INFRA_PATTERNS)


_PYTEST_FAILED_RE = re.compile(
    r"^FAILED\s+(?P<path>[\w./-]+\.py)::(?P<test>[\w\[\]./:-]+?)"
    r"(?:\s+-\s+(?P<message>.*))?$"
)


def parse_pytest_failures(log_text: str) -> list[ParsedFailure]:
    """Extract pytest failures from a ``FAILED <path>::<test> - <reason>`` log tail.

    Matches pytest's "short test summary info" section, which lists one
    ``FAILED`` line per failing test regardless of how it's invoked
    (unit/integration), so this one parser covers both.
    """
    failures: list[ParsedFailure] = []
    for line in log_text.splitlines():
        match = _PYTEST_FAILED_RE.match(line.strip())
        if not match:
            continue
        signature = f"{match.group('path')}::{match.group('test')}"
        message = match.group("message")
        failures.append(
            ParsedFailure(
                test_type="pytest",
                signature=signature,
                error_message=message,
                is_environmental=bool(message) and detect_infra_error(message),
            )
        )
    return failures


_GO_FAIL_RE = re.compile(r"^---\s+FAIL:\s+(?P<test>\S+)\s+\(")
_GO_PACKAGE_SUMMARY_RE = re.compile(r"^FAIL\t(?P<package>\S+)\t")


def parse_go_failures(log_text: str) -> list[ParsedFailure]:
    r"""Extract Go test failures as ``package.TestName``.

    ``go test -v`` prints ``--- FAIL: TestName (0.01s)`` as each test
    finishes, then one ``FAIL\t<package>\t<duration>`` summary line per
    package once all its tests have run. The package isn't known until that
    summary line appears, so failing test names are buffered until the next
    package summary (or ``ok``) line resolves which package they belong to.
    """
    failures: list[ParsedFailure] = []
    pending: list[str] = []
    for line in log_text.splitlines():
        fail_match = _GO_FAIL_RE.match(line)
        if fail_match:
            pending.append(fail_match.group("test"))
            continue
        pkg_match = _GO_PACKAGE_SUMMARY_RE.match(line)
        if pkg_match and pending:
            package = pkg_match.group("package")
            failures.extend(
                ParsedFailure(test_type="go", signature=f"{package}.{test_name}")
                for test_name in pending
            )
            pending = []
    # A log tail that was truncated before the package summary line still
    # arrived: report bare test names rather than silently dropping them.
    failures.extend(
        ParsedFailure(test_type="go", signature=test_name) for test_name in pending
    )
    return failures


_SPREAD_ERROR_RE = re.compile(
    # Real spread runs prefix the task with "<backend>:<system>:" (e.g.
    # "openstack:ubuntu-24.04-64:"), which is dropped rather than captured
    # so the same task failing on different backends/Ubuntu versions is one
    # signature, not a new one per version. Both components are restricted
    # to word/dot/hyphen characters (no "/") since the suite/task path
    # always contains at least one "/", so the two can't be confused.
    r"Error executing (?:[\w.-]+:[\w.-]+:)?(?P<suite_task>[\w./:-]+)"
    r"\s*\((?P<run_id>[\w-]+)\)"
    r"(?:\s*:\s*(?P<message>.*))?"
)


def _extract_spread_error_block(lines: list[str], error_line_index: int) -> str | None:
    """Capture the multi-line error block Spread prints after "Error executing ...".

    Real Spread output never puts the error detail on the same line as the
    colon; instead it wraps whatever the task printed (shell trace,
    assertion, traceback) between a pair of bare "-----" delimiter lines
    immediately following the "Error executing" line.
    """
    if (
        error_line_index + 1 >= len(lines)
        or lines[error_line_index + 1].strip() != "-----"
    ):
        return None
    block: list[str] = []
    for line in lines[error_line_index + 2 :]:
        if line.strip() == "-----":
            break
        block.append(line)
    return "\n".join(block) if block else None


def parse_spread_failures(log_text: str) -> list[ParsedFailure]:
    """Extract Spread task failures as ``tests/spread/<suite>/<task>[:<variant>]``.

    The trailing ``(<run-id>)`` is captured separately and discarded from the
    signature so the same task failing on different runs is recognized as
    the same signature rather than a new one each time.
    """
    failures: list[ParsedFailure] = []
    lines = log_text.splitlines()
    for index, line in enumerate(lines):
        match = _SPREAD_ERROR_RE.search(line)
        if not match:
            continue
        message = match.group("message") or _extract_spread_error_block(lines, index)
        is_env = bool(message) and detect_infra_error(message)
        failures.append(
            ParsedFailure(
                test_type="spread",
                signature=match.group("suite_task"),
                error_message=message,
                is_environmental=is_env,
            )
        )
    return failures


# Known lint/QA/docs/security tools that show up as their own failing steps
# across the craft projects. This list is deliberately small and will need
# to grow as new tools are observed in real failing logs -- see the open
# question in plans/44-ci-failure-catalog.md about unconfirmed log formats.
#
# These parsers are these jobs' *only* source of a real signature: in
# production, every Check Run Annotation ever recorded for a lint/QA/
# security job has ``path == ".github/workflows/<file>.yml"`` (GitHub's own
# generic "this step failed" marker, not the tool's own diagnostic) with a
# message like "Process completed with exit code 1." -- no file, line, or
# rule the tool actually flagged. Annotations only carry real content when a
# project wires up a dedicated annotation-posting action, which none of the
# tracked repos currently do.
# OSV-scanner reports each finding against a stable vulnerability ID
# (OSV/GitHub Security Advisory or CVE), unlike the surrounding table
# formatting, which can shift between scans.
_VULN_ID_RE = re.compile(
    r"\b(?:CVE-\d{4}-\d+|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})\b"
)
# Matches real scan output (a vuln ID, or "N vulnerabilities found"/"no
# vulnerabilities data available") but not GitHub Actions' own boilerplate
# for the step/job named "osv-scanner" (install confirmation, command
# echoes, ``##[group]`` markers) -- none of those mention a vulnerability
# by name, but all contain the literal substring "osv-scanner" and would
# otherwise be treated as a (never-repeating, timestamped) failure.
_OSV_SCANNER_RE = re.compile(
    rf"{_VULN_ID_RE.pattern}|\bvulnerabilit(?:y|ies)\b", re.IGNORECASE
)

_LINTER_TOOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Matches both ruff's own failure-summary line and an individual
    # violation line (``path.py:LINE:COL: CODE ...``), since ruff doesn't
    # repeat its own name on every violation it reports.
    (
        "ruff",
        re.compile(r"\bruff\b.*error|^\S+\.py:\d+:\d+:\s+[A-Z]+\d+", re.IGNORECASE),
    ),
    ("osv-scanner", _OSV_SCANNER_RE),
    ("docs-lint", re.compile(r"docs-lint|linkcheck", re.IGNORECASE)),
    ("vale", re.compile(r"\bvale\b", re.IGNORECASE)),
    ("prettier", re.compile(r"\bprettier\b", re.IGNORECASE)),
    ("tombi", re.compile(r"\btombi\b", re.IGNORECASE)),
    ("conventional-commits", re.compile(r"conventional.?commits?", re.IGNORECASE)),
)

# A ruff violation line looks like "path/to/file.py:12:5: E501 message" --
# the line:col shifts whenever code above it changes, so only path+code is
# kept for the signature.
_RUFF_VIOLATION_RE = re.compile(r"^(?P<path>[\w./-]+):\d+:\d+:\s+(?P<code>[A-Z]+\d+)\b")

# GitHub Actions' own step-level formatting, never a tool's real output:
# ``##[group]``/``##[endgroup]`` fold markers, and the bright-cyan-bold
# ANSI color (``\x1b[36;1m``) the runner wraps around a step's command
# before executing it. A tool installed via ``snap install <name>`` or
# invoked as ``<name> scan ...`` puts its own name in that command-echo
# line, which would otherwise match every tool pattern above and -- being
# a different command each run -- never produce a repeatable signature.
_GHA_STEP_BOILERPLATE_RE = re.compile(r"^##\[(?:group|endgroup)\]|\x1b\[36;1m")


def _stable_tool_signature(tool: str, line: str, fallback: str) -> str:
    """Normalize a known tool's log line to a signature stable across runs.

    Falls back to the raw truncated line when no stable identifier can be
    extracted (unrecognized output format, or a line that doesn't carry one),
    so an unparsed line still produces *a* signature rather than none.
    """
    if tool == "ruff":
        match = _RUFF_VIOLATION_RE.match(line)
        if match:
            return f"ruff: {match.group('path')} {match.group('code')}"
    elif tool == "osv-scanner":
        match = _VULN_ID_RE.search(line)
        if match:
            return f"osv-scanner: {match.group(0)}"
    return fallback


def parse_linter_and_doc_failures(log_text: str) -> list[ParsedFailure]:
    """Extract known linter/QA/docs/security tool failures from a log tail.

    Unlike pytest/Go/Spread, these tools don't share one output grammar, so
    this matches a small known-tool allowlist rather than a single regex.
    One signature is emitted per matching line (deduplicated by content).
    """
    seen: set[str] = set()
    failures: list[ParsedFailure] = []
    for line in log_text.splitlines():
        stripped = line.strip()
        if not stripped or _GHA_STEP_BOILERPLATE_RE.search(stripped):
            continue
        for tool, pattern in _LINTER_TOOL_PATTERNS:
            if not pattern.search(stripped):
                continue
            signature = _stable_tool_signature(
                tool, stripped, fallback=f"{tool}: {stripped[:200]}"
            )
            if signature in seen:
                continue
            seen.add(signature)
            failures.append(
                ParsedFailure(
                    test_type="linter",
                    signature=signature,
                    error_message=stripped,
                )
            )
            break
    return failures


def parse_check_annotations(
    annotations: list[dict[str, str | int | None]],
) -> list[ParsedFailure]:
    """Build failures from GitHub Check Run Annotations.

    Used as a last-resort fallback for jobs with no known framework/tool
    parser (see ``has_structured_parser_hint``), or when a known job's log
    is unavailable: the signature is simply ``path:line``, which in
    practice is GitHub's own generic step-failure marker rather than a
    tool's own diagnostic (see ``_LINTER_TOOL_PATTERNS``'s comment), so it
    carries much less signal than a parsed log line.

    Args:
        annotations: Dicts with ``path``, ``start_line``, ``message``, and
            ``annotation_level`` keys, matching the shape of
            ``github.CheckRunAnnotation.CheckRunAnnotation.raw_data``.

    """
    failures: list[ParsedFailure] = []
    for annotation in annotations:
        if annotation.get("annotation_level") != "failure":
            continue
        path = str(annotation.get("path") or "unknown")
        line = annotation.get("start_line")
        message = str(annotation.get("message") or "")
        signature = f"{path}:{line}" if line is not None else path
        failures.append(
            ParsedFailure(
                test_type="annotation",
                signature=signature,
                error_message=message or None,
                is_environmental=bool(message) and detect_infra_error(message),
            )
        )
    return failures


_SPREAD_HINTS = ("spread",)
_PYTEST_HINTS = ("pytest", "unit", "integration", "test")
_GO_HINTS = ("go", "golang")
_LINTER_HINTS = ("lint", "qa", "security", "scan", "polic", "tics")


def has_structured_parser_hint(job_name: str) -> bool:
    """Return whether ``job_name`` matches a parser with a stable signature.

    Used by the collector to decide whether a job's log is worth
    downloading even when Check Run Annotations are available. In
    production, every Check Run Annotation ever recorded carries no real
    diagnostic content -- GitHub's own generic "this step failed" marker,
    pointing at the workflow YAML rather than the tool's actual output (see
    ``_LINTER_TOOL_PATTERNS``'s comment) -- so a job's log is the only
    source of a signature worth keeping, for every known framework/tool
    covered by a parser below, not just pytest/spread/Go.
    """
    lowered = job_name.lower()
    return any(
        hint in lowered
        for hint in (*_SPREAD_HINTS, *_PYTEST_HINTS, *_GO_HINTS, *_LINTER_HINTS)
    )


def parse_log_tail(log_text: str, *, job_name: str) -> list[ParsedFailure]:
    """Dispatch a job's log tail to the parser(s) matching its job name.

    Falls back to trying every parser when the job name doesn't hint at a
    specific framework, since a single job can run more than one tool (e.g.
    a combined "QA" job running both ruff and docs-lint).
    """
    log_text = _strip_gha_timestamps(log_text)
    lowered = job_name.lower()
    results: list[ParsedFailure] = []
    if any(hint in lowered for hint in _SPREAD_HINTS):
        results.extend(parse_spread_failures(log_text))
    if any(hint in lowered for hint in _PYTEST_HINTS):
        results.extend(parse_pytest_failures(log_text))
    if any(hint in lowered for hint in _GO_HINTS):
        results.extend(parse_go_failures(log_text))
    if not results:
        results.extend(parse_pytest_failures(log_text))
        results.extend(parse_go_failures(log_text))
        results.extend(parse_spread_failures(log_text))
    results.extend(parse_linter_and_doc_failures(log_text))
    return results
