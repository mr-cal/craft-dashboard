"""Launchpad data collector for bugs."""

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from launchpadlib.launchpad import Launchpad
    from lazr.restfulclient.resource import Entry

from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.collectors import ISSUE_UPSERT_FIELDS
from craft_dashboard.llm.content_hash import compute_content_hash

__all__ = ["LaunchpadCollector"]

logger = logging.getLogger(__name__)

#: Maximum number of recent bug comments to store per Launchpad bug,
#: matching the shape (but not necessarily the count) of GitHub's
#: `_fetch_issue_comments`.
_MAX_COMMENTS = 50

#: Overlap subtracted from the stored watermark when computing
#: `modified_since`. Launchpad's `date_last_updated` and our own clock can
#: drift, and a bug modified during the seconds around a run boundary would
#: otherwise fall between two windows and never be re-fetched.
_WATERMARK_OVERLAP = timedelta(hours=1)

_OPEN_STATUSES = frozenset(
    {
        "New",
        "Confirmed",
        "Triaged",
        "In Progress",
        "Incomplete",
        "Incomplete (with response)",
        "Incomplete (without response)",
    }
)

_CLOSED_STATUSES = frozenset(
    {
        "Fix Released",
        "Fix Committed",
        "Invalid",
        "Won't Fix",
        "Expired",
        "Opinion",
    }
)


def _map_lp_status(lp_status: str) -> str:
    """Map a Launchpad bug status to a normalized state.

    Args:
        lp_status: The Launchpad bug task status string.

    Returns:
        'open' or 'closed'.

    """
    if lp_status in _CLOSED_STATUSES:
        return "closed"
    return "open"


def _fetch_bug_comments(bug: "Entry") -> list[dict]:
    """Fetch the last 50 messages on a Launchpad bug as comment dicts.

    The first entry in ``bug.messages`` is the bug description itself (not
    a reply), so it's skipped to avoid duplicating ``Issue.body``.

    Args:
        bug: A launchpadlib Bug resource.

    Returns:
        List of comment dicts, each with author/body/created_at/type.

    """
    messages = list(bug.messages)[1:]
    recent = messages[-_MAX_COMMENTS:]
    return [
        {
            "author": str(m.owner_link).rsplit("/", 1)[-1]
            if m.owner_link
            else "unknown",
            "body": (m.content or "")[:1000],
            "created_at": m.date_created.replace(tzinfo=UTC).isoformat()
            if m.date_created
            else None,
            "type": "comment",
        }
        for m in recent
    ]


class LaunchpadCollector:
    """Collects bug data from Launchpad."""

    def __init__(
        self,
        projects: list[str] | None = None,
        launchpad_maintainers: list[str] | None = None,
    ) -> None:
        """Initialize the Launchpad collector.

        Args:
            projects: List of Launchpad project names to collect from.
            launchpad_maintainers: List of Launchpad usernames considered maintainers.

        """
        self.projects = projects or []
        self._maintainers: set[str] = set(launchpad_maintainers or [])
        self._lp = None

    def _get_launchpad(self) -> "Launchpad":
        """Lazily initialize the Launchpad API client.

        Returns:
            A launchpadlib Launchpad instance.

        """
        if self._lp is None:
            from launchpadlib.launchpad import (
                Launchpad,
            )

            self._lp = Launchpad.login_anonymously(
                "craft-dashboard", "production", version="devel"
            )
        return self._lp

    async def collect_bugs(
        self,
        lp_project_name: str,
        project_id: int,
        session: AsyncSession,
        collection_run_id: int | None = None,
        *,
        full_refresh: bool = False,
    ) -> int:
        """Collect bugs for a Launchpad project.

        Args:
            lp_project_name: Launchpad project name.
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            collection_run_id: ID of the collection run that fetched these bugs.
            full_refresh: Ignore the stored watermark and re-fetch every bug.
                Needed to repair gaps left by a run that advanced the floor
                past bugs it never actually fetched.

        Returns:
            The number of bugs upserted.

        """
        from sqlalchemy import (
            select,
        )
        from sqlalchemy.dialects.postgresql import (
            insert,
        )

        from craft_dashboard.models.collection_watermark import (
            CollectionWatermark,
        )
        from craft_dashboard.models.issue import (
            Issue,
        )
        from craft_dashboard.models.issue_activity import (
            IssueActivity,
        )

        lp = self._get_launchpad()
        project = lp.projects[lp_project_name]

        # Incremental fetch: only retrieve bugs modified since the last
        # *successful* collection. The watermark is written by the collection
        # driver only after the whole project finishes, so a run that dies
        # partway through does not advance it. Deriving the floor from
        # `max(Issue.last_fetched_at)` instead would advance it on every
        # partial run, permanently skipping bugs modified in the gap.
        # On the first run (no watermark) we fetch everything.
        last_collected = None
        if not full_refresh:
            last_collected = await session.scalar(
                select(CollectionWatermark.last_collected_at).where(
                    CollectionWatermark.project_id == project_id,
                    CollectionWatermark.source == "launchpad",
                )
            )

        search_kwargs: dict = {"status": list(_OPEN_STATUSES | _CLOSED_STATUSES)}
        if last_collected is not None:
            modified_since = last_collected - _WATERMARK_OVERLAP
            search_kwargs["modified_since"] = modified_since
            logger.info(
                "Incremental Launchpad fetch for %s since %s "
                "(watermark %s minus %s overlap)",
                lp_project_name,
                modified_since,
                last_collected,
                _WATERMARK_OVERLAP,
            )
        else:
            logger.info(
                "Full Launchpad fetch for %s (%s)",
                lp_project_name,
                "--full-refresh" if full_refresh else "no watermark",
            )

        bug_tasks = project.searchTasks(**search_kwargs)
        count = 0
        activity_recorded = 0

        for task in bug_tasks:
            bug = task.bug
            state = _map_lp_status(task.status)
            author = (
                str(task.owner_link).rsplit("/", 1)[-1] if task.owner_link else None
            )
            author_is_maintainer = author in self._maintainers if author else False
            labels = list(bug.tags)

            try:
                comments = _fetch_bug_comments(bug)
            except Exception:  # noqa: BLE001 - a single bug's comments should not abort collection
                logger.warning(
                    "Failed to fetch comments for Launchpad bug %s",
                    bug.id,
                    exc_info=True,
                )
                comments = []

            existing = await session.execute(
                select(
                    Issue.last_fetched_at, Issue.closed_at, Issue.content_hash
                ).where(
                    Issue.project_id == project_id,
                    Issue.source == "launchpad",
                    Issue.external_id == str(bug.id),
                )
            )
            existing_row = (
                existing.one_or_none()
                if existing is not None and hasattr(existing, "one_or_none")
                else None
            )
            if existing_row and isinstance(existing_row, (tuple, list)):
                last_fetched_item, previous_closed_at, previous_hash = existing_row
            else:
                last_fetched_item, previous_closed_at, previous_hash = None, None, None

            content_hash = compute_content_hash(
                bug.title, bug.description, state, labels, comments=comments
            )

            # Only record activity when something actually changed. The
            # incremental query re-fetches bugs whose Launchpad metadata moved
            # for reasons we do not store (and re-fetches the overlap window
            # every run), so an unconditional insert would fabricate a stream
            # of "updated" events and grow `issue_activity` without bound.
            change_type: str | None
            if last_fetched_item is None:
                change_type = "created"
            elif state == "closed" and previous_closed_at is None:
                change_type = "closed"
            elif previous_hash != content_hash:
                change_type = "updated"
            else:
                change_type = None

            if change_type is not None:
                occurred_at = (
                    bug.date_last_updated.replace(tzinfo=UTC)
                    if bug.date_last_updated
                    else datetime.now(tz=UTC)
                )
                session.add(
                    IssueActivity(
                        project_id=project_id,
                        issue_number=bug.id,
                        change_type=change_type,
                        title=(bug.title or "")[:200],
                        occurred_at=occurred_at,
                        collection_run_id=collection_run_id,
                    )
                )
                activity_recorded += 1

            stmt = insert(Issue).values(
                project_id=project_id,
                source="launchpad",
                external_id=str(bug.id),
                issue_type="issue",
                title=bug.title,
                body=bug.description,
                state=state,
                author=author,
                author_is_maintainer=author_is_maintainer,
                author_is_bot=False,
                labels=labels,
                comments=comments,
                created_at=bug.date_created.replace(tzinfo=UTC)
                if bug.date_created
                else None,
                updated_at=bug.date_last_updated.replace(tzinfo=UTC)
                if bug.date_last_updated
                else None,
                closed_at=task.date_closed.replace(tzinfo=UTC)
                if hasattr(task, "date_closed") and task.date_closed
                else None,
                url=bug.web_link,
                metadata_={"importance": task.importance, "status": task.status},
                content_hash=content_hash,
                last_fetched_at=datetime.now(tz=UTC),
                collection_run_id=collection_run_id,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "source", "external_id"],
                set_={
                    field: getattr(stmt.excluded, field)
                    for field in ISSUE_UPSERT_FIELDS
                }
                | {
                    "metadata": stmt.excluded.metadata,
                    "comments": stmt.excluded.comments,
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        logger.info(
            "Collected %d bugs from Launchpad project %s (%d activity events)",
            count,
            lp_project_name,
            activity_recorded,
        )
        return count
