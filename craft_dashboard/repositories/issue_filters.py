"""Shared issue-exclusion predicates.

`craft-dashboard.toml` can list issue numbers to hide from the dashboard
entirely. Every query that reads issues or their activity must apply the same
exclusion, so the predicates live here rather than in any one caller: routes,
services, repositories and the evaluation queue all import from this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, cast, or_

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.issue_activity import IssueActivity
from craft_dashboard.models.project import Project

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement

__all__ = [
    "build_excluded_activity_condition",
    "build_excluded_issues_condition",
]


def build_excluded_issues_condition(
    filtered_issues: dict[str, list[str]],
) -> ColumnElement[bool] | None:
    """Return a NOT(...) clause excluding configured issue numbers.

    Returns None when filtered_issues is empty (no WHERE clause needed).
    Requires that Issue and Project are already joined in the calling query.
    """
    conditions = [
        (Project.name == project_name) & Issue.external_id.in_(ids)
        for project_name, ids in filtered_issues.items()
        if ids
    ]
    if not conditions:
        return None
    combined = or_(*conditions) if len(conditions) > 1 else conditions[0]
    return ~combined


def build_excluded_activity_condition(
    filtered_issues: dict[str, list[str]],
) -> ColumnElement[bool] | None:
    """Return a NOT(...) clause excluding configured issue numbers from IssueActivity.

    Mirrors :func:`build_excluded_issues_condition` but matches on
    ``IssueActivity.issue_number`` (an integer column) instead of
    ``Issue.external_id``, since the activity feed query only outer-joins
    ``Issue`` (the issue row may no longer exist) and must not depend on that
    join to apply the exclusion. Requires ``Project`` to already be joined in
    the calling query. Returns None when filtered_issues is empty.
    """
    conditions = [
        (Project.name == project_name)
        & cast(IssueActivity.issue_number, String).in_(ids)
        for project_name, ids in filtered_issues.items()
        if ids
    ]
    if not conditions:
        return None
    combined = or_(*conditions) if len(conditions) > 1 else conditions[0]
    return ~combined
