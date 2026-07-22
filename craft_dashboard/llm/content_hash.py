"""Shared content-hash computation for issue/PR change detection.

This hash is used two ways:

- Stored on ``Issue.content_hash``, kept up to date by the collectors on
  every create/update, so "does this issue need evaluation" is a plain
  column comparison against ``LLMEvaluation.issue_data_hash`` rather than
  something that has to be recomputed for every issue on every poll.
- Stored on ``LLMEvaluation.issue_data_hash`` at evaluation time, so drift
  between "what the issue looked like when evaluated" and "what it looks
  like now" can still be detected even if ``Issue.content_hash`` itself was
  somehow not updated.

.. warning::
    Any change to ``compute_content_hash``'s output MUST be accompanied by a
    ``CURRENT_EVAL_VERSION`` bump (see ``craft_dashboard.llm.evaluator``) in
    the same commit. Changing the hash without bumping the version causes
    all existing evaluations to appear stale, triggering mass re-evaluation
    of issues that haven't actually changed.
"""

from __future__ import annotations

import hashlib
from typing import Any

IssueComment = dict[str, Any]

#: pr_details keys that represent reviewer/CI intent a human or the LLM would
#: act on. Diff stats and other cosmetic fields are deliberately excluded so
#: routine force-pushes without new review activity don't force re-evaluation.
HASHED_PR_DETAIL_KEYS = (
    "review_status",
    "review_count",
    "unresolved_review_comments",
    "ci_passing",
    "ci_failing",
    "ci_pending",
)


def compute_content_hash(
    title: str,
    body: str | None,
    state: str,
    labels: list[str],
    comments: list[IssueComment] | None = None,
    pr_details: dict[str, Any] | None = None,
) -> str:
    """Compute a SHA-256 hash of issue content for change detection.

    Includes comments so that new discussion triggers re-evaluation, and a
    subset of PR review/CI metadata (see ``HASHED_PR_DETAIL_KEYS``) so that a
    review approval, changes-requested, or CI status flip triggers
    re-evaluation even when the title/body/labels/comments are unchanged.

    Args:
        title: Issue title.
        body: Issue body text.
        state: Issue state.
        labels: List of label names.
        comments: Recent comments list (optional).
        pr_details: PR review/CI/diff metadata dict (optional). Only the keys
            listed in ``HASHED_PR_DETAIL_KEYS`` affect the hash.

    Returns:
        A 64-character hex string.

    """
    comments_repr = ""
    if comments:
        comments_repr = "|" + ";".join(
            f"{c.get('author', '')}:{c.get('body', '')[:100]}"
            for c in sorted(comments, key=lambda c: c.get("created_at") or "")
        )
    pr_details_repr = ""
    if pr_details:
        pr_details_repr = "|" + ";".join(
            f"{key}={pr_details[key]!r}"
            for key in HASHED_PR_DETAIL_KEYS
            if key in pr_details
        )
    content = (
        f"{title}|{body or ''}|{state}|{','.join(sorted(labels))}"
        f"{comments_repr}{pr_details_repr}"
    )
    return hashlib.sha256(content.encode()).hexdigest()
