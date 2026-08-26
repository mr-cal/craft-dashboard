"""Pure, I/O-free extraction of issue references from commit text.

Precision tiers, from the design doc (section 4):
- QualifiedRef ("canonical/craft-parts#567" or a full GitHub issue/PR URL):
  exact cross-repo match, resolves directly to (project, external_id).
- BareRef ("#1234"): repo-scoped for exact matching (implies the commit's
  own repo); NEVER an exact match against another project's issue #1234,
  since issue numbers are not globally unique. Contributes only a weak
  cross-repo candidate signal, disambiguated later by the model via the
  issue_detail("#1234") tool (Phase 4).
- LaunchpadRef ("LP: #567"): cross-*source*, not cross-repo — resolves
  against launchpad-projects (currently only snapcraft).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_QUALIFIED_SLASH_RE = re.compile(r"\b([\w.-]+)/([\w.-]+)#(\d+)\b")
_QUALIFIED_URL_RE = re.compile(
    r"https://github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)\b"
)
_BARE_HASH_RE = re.compile(r"(?<![\w/])#(\d+)\b")
_LP_REF_RE = re.compile(r"\bLP:\s*#(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class QualifiedRef:
    """An exact cross-repo reference, e.g. canonical/craft-parts#567."""

    project: str
    external_id: str


@dataclass(frozen=True)
class BareRef:
    """A same-repo-implied reference, e.g. #1234 with no owner/repo prefix."""

    external_id: str


@dataclass(frozen=True)
class LaunchpadRef:
    """A Launchpad bug reference, e.g. LP: #2012345."""

    external_id: str


@dataclass
class ExtractedReferences:
    """All references found in one piece of commit text."""

    qualified: list[QualifiedRef]
    bare: list[BareRef]
    launchpad: list[LaunchpadRef]


def extract_references(text: str) -> ExtractedReferences:
    """Extract all qualified, bare, and Launchpad references from *text*.

    Args:
        text: A commit message (subject + body).

    Returns:
        ExtractedReferences with each kind of match, deduplicated within
        its own list but not across lists. A qualified ref's #N portion and
        an `LP: #N` ref's #N portion are both deliberately excluded from the
        bare list (see the `consumed_spans` exclusion below), since each is
        already precisely resolved by its own, higher-precision signal.

    """
    qualified: list[QualifiedRef] = []
    consumed_spans: list[tuple[int, int]] = []

    for match in _QUALIFIED_SLASH_RE.finditer(text):
        owner, repo, external_id = match.groups()
        ref = QualifiedRef(project=repo, external_id=external_id)
        if ref not in qualified:
            qualified.append(ref)
        consumed_spans.append(match.span())

    for match in _QUALIFIED_URL_RE.finditer(text):
        _owner, repo, external_id = match.groups()
        ref = QualifiedRef(project=repo, external_id=external_id)
        if ref not in qualified:
            qualified.append(ref)
        consumed_spans.append(match.span())

    # Extract LP refs *before* bare refs and record their spans, so the #N
    # inside "LP: #2012345" is not also emitted as a weak bare-ref signal.
    launchpad: list[LaunchpadRef] = []
    for match in _LP_REF_RE.finditer(text):
        ref = LaunchpadRef(external_id=match.group(1))
        if ref not in launchpad:
            launchpad.append(ref)
        consumed_spans.append(match.span())

    bare: list[BareRef] = []
    for match in _BARE_HASH_RE.finditer(text):
        span = match.span()
        # Skip a #N that overlaps an already-consumed span: the "#567"
        # inside "canonical/craft-parts#567", or the "#2012345" inside
        # "LP: #2012345". Overlap (not just start containment) is used so a
        # bare match that begins inside any consumed span is dropped.
        if any(start <= span[0] < end for start, end in consumed_spans):
            continue
        ref = BareRef(external_id=match.group(1))
        if ref not in bare:
            bare.append(ref)

    return ExtractedReferences(qualified=qualified, bare=bare, launchpad=launchpad)
