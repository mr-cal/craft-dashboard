"""PR velocity and first-response metrics with 12-month baselines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project
from craft_dashboard.services.dashboard.common import (
    days_since,
    to_utc,
    to_utc_or_none,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy import ColumnElement, Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.services.dashboard.types import PRVelocity

BASELINE_CHECKPOINT_COUNT = 12


@dataclass
class OpenPRAge:
    """Classification of a single open PR at the current moment."""

    created: datetime
    age_days: int
    is_contributor: bool
    is_waiting: bool


@dataclass
class OpenPRAgeBuckets:
    """Age samples for open PRs, split by contributor and waiting status."""

    contributor: list[int] = field(default_factory=list)
    waiting: list[int] = field(default_factory=list)
    overall: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class VelocityBaselines:
    """Rounded 12-month baselines for the three velocity measures."""

    contributor: int | None
    waiting: int | None
    overall: int | None


def _average_or_none(samples: Sequence[int]) -> int | None:
    """Return the rounded mean of the samples, or None when empty."""
    return int(round(sum(samples) / len(samples))) if samples else None


def _delta(current: int | None, baseline: int | None) -> int | None:
    """Return current minus baseline when both values are known."""
    if current is None or baseline is None:
        return None
    return current - baseline


def build_open_pr_query(excl: ColumnElement[bool] | None) -> Select[Any]:
    """Return the base query selecting open PRs for velocity computations."""
    query = (
        select(
            Issue.id,
            Issue.created_at,
            Issue.author_is_maintainer,
            Issue.author_is_bot,
            Issue.metadata_,
            Issue.comments,
        )
        .join(Project, Issue.project_id == Project.id)
        .where(
            Issue.state == "open",
            Issue.issue_type == "pull_request",
            Project.category != "aggregate",
        )
    )
    if excl is not None:
        query = query.where(excl)
    return query


def iter_open_pr_ages(
    rows: Sequence[Any],
    now: datetime,
    maintainers_set: set[str],
) -> Iterator[tuple[Any, OpenPRAge]]:
    """Yield each open PR row paired with its age classification."""
    for row in rows:
        if row.created_at is None:
            continue
        created = to_utc(row.created_at)
        age_days = days_since(now, created)
        is_contributor = not row.author_is_maintainer and not row.author_is_bot
        is_waiting = False
        if is_contributor:
            comments = row.comments or []
            has_maintainer_response = any(
                isinstance(c, dict) and c.get("author") in maintainers_set
                for c in comments
            )
            review_count = (
                (row.metadata_ or {}).get("review_count", 0)
                if isinstance(row.metadata_, dict)
                else 0
            )
            is_waiting = not has_maintainer_response and review_count == 0
        yield row, OpenPRAge(created, age_days, is_contributor, is_waiting)


def collect_open_pr_ages(
    rows: Sequence[Any],
    now: datetime,
    maintainers_set: set[str],
) -> OpenPRAgeBuckets:
    """Bucket open PR ages into contributor, waiting, and overall samples."""
    buckets = OpenPRAgeBuckets()
    for _row, info in iter_open_pr_ages(rows, now, maintainers_set):
        buckets.overall.append(info.age_days)
        if info.is_contributor:
            buckets.contributor.append(info.age_days)
            if info.is_waiting:
                buckets.waiting.append(info.age_days)
    return buckets


async def fetch_pr_history_rows(
    session: AsyncSession,
    excl: ColumnElement[bool] | None,
    one_year_ago: datetime,
) -> Sequence[Any]:
    """Fetch PRs that were open at some point during the trailing year."""
    query = (
        select(
            Issue.id,
            Issue.created_at,
            Issue.closed_at,
            Issue.author_is_maintainer,
            Issue.author_is_bot,
            Issue.comments,
        )
        .join(Project, Issue.project_id == Project.id)
        .where(
            Issue.issue_type == "pull_request",
            Project.category != "aggregate",
            or_(
                Issue.state == "open",
                Issue.closed_at >= one_year_ago,
            ),
        )
    )
    if excl is not None:
        query = query.where(excl)
    return (await session.execute(query)).all()


def _had_maintainer_response_by(
    comments: Sequence[Any],
    maintainers_set: set[str],
    checkpoint: datetime,
) -> bool:
    """Return whether a maintainer had commented on or before the checkpoint."""
    for c in comments:
        if isinstance(c, dict) and c.get("author") in maintainers_set:
            c_time = c.get("created_at")
            try:
                if c_time and datetime.fromisoformat(c_time).astimezone(UTC) <= (
                    checkpoint
                ):
                    return True
            except (ValueError, TypeError):
                return True
    return False


def compute_velocity_baselines(
    history_rows: Sequence[Any],
    now: datetime,
    maintainers_set: set[str],
) -> VelocityBaselines:
    """Average open-PR ages across 12 monthly checkpoints to form baselines."""
    checkpoints = [
        now - timedelta(days=30 * i) for i in range(1, BASELINE_CHECKPOINT_COUNT + 1)
    ]
    hist_contrib_avgs: list[float] = []
    hist_waiting_avgs: list[float] = []
    hist_overall_avgs: list[float] = []

    for cp in checkpoints:
        cp_contrib: list[int] = []
        cp_waiting: list[int] = []
        cp_overall: list[int] = []
        for prow in history_rows:
            p_created = to_utc_or_none(prow.created_at)
            p_closed = to_utc_or_none(prow.closed_at)
            if (
                p_created is not None
                and p_created <= cp
                and (p_closed is None or p_closed > cp)
            ):
                age = max(0, (cp - p_created).days)
                cp_overall.append(age)
                if not prow.author_is_maintainer and not prow.author_is_bot:
                    cp_contrib.append(age)
                    if not _had_maintainer_response_by(
                        prow.comments or [], maintainers_set, cp
                    ):
                        cp_waiting.append(age)
        if cp_contrib:
            hist_contrib_avgs.append(sum(cp_contrib) / len(cp_contrib))
        if cp_waiting:
            hist_waiting_avgs.append(sum(cp_waiting) / len(cp_waiting))
        if cp_overall:
            hist_overall_avgs.append(sum(cp_overall) / len(cp_overall))

    return VelocityBaselines(
        contributor=_mean_baseline(hist_contrib_avgs),
        waiting=_mean_baseline(hist_waiting_avgs),
        overall=_mean_baseline(hist_overall_avgs),
    )


def _mean_baseline(averages: Sequence[float]) -> int | None:
    """Return the rounded mean of per-checkpoint averages, or None when empty."""
    if not averages:
        return None
    return int(round(sum(averages) / len(averages)))


def build_velocity(
    buckets: OpenPRAgeBuckets,
    baselines: VelocityBaselines,
) -> PRVelocity:
    """Assemble the velocity payload from current samples and baselines."""
    contrib_avg = _average_or_none(buckets.contributor)
    waiting_avg = _average_or_none(buckets.waiting)
    overall_avg = _average_or_none(buckets.overall)

    return {
        "contributor_count": len(buckets.contributor),
        "contributor_avg_age": contrib_avg,
        "contributor_avg_delta": _delta(contrib_avg, baselines.contributor),
        "contributor_avg_baseline": baselines.contributor,
        "first_response_waiting_count": len(buckets.waiting),
        "first_response_avg_days": waiting_avg,
        "first_response_avg_delta": _delta(waiting_avg, baselines.waiting),
        "first_response_avg_baseline": baselines.waiting,
        "overall_count": len(buckets.overall),
        "overall_avg_age": overall_avg,
        "overall_avg_delta": _delta(overall_avg, baselines.overall),
        "overall_avg_baseline": baselines.overall,
    }


async def compute_pr_velocity(
    session: AsyncSession,
    maintainers_set: set[str],
    excl: ColumnElement[bool] | None,
    now: datetime,
    one_year_ago: datetime,
) -> PRVelocity:
    """Compute open PR velocity and first-response metrics for the homepage."""
    pr_rows = (await session.execute(build_open_pr_query(excl))).all()
    buckets = collect_open_pr_ages(pr_rows, now, maintainers_set)
    history_rows = await fetch_pr_history_rows(session, excl, one_year_ago)
    baselines = compute_velocity_baselines(history_rows, now, maintainers_set)
    return build_velocity(buckets, baselines)
