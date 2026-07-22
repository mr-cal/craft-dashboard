"""Launchpad data collector for bugs."""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from launchpadlib.launchpad import Launchpad

from sqlalchemy.ext.asyncio import AsyncSession

from craft_dashboard.collectors import ISSUE_UPSERT_FIELDS
from craft_dashboard.llm.content_hash import compute_content_hash

__all__ = ["LaunchpadCollector"]

logger = logging.getLogger(__name__)

_OPEN_STATUSES = frozenset(
    {
        "New",
        "Confirmed",
        "Triaged",
        "In Progress",
        "Incomplete",
        "Opinion",
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
    ) -> int:
        """Collect bugs for a Launchpad project.

        Args:
            lp_project_name: Launchpad project name.
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.
            collection_run_id: ID of the collection run that fetched these bugs.

        Returns:
            The number of bugs upserted.

        """
        from sqlalchemy import (
            func,
            select,
        )
        from sqlalchemy.dialects.postgresql import (
            insert,
        )

        from craft_dashboard.models.issue import (
            Issue,
        )

        lp = self._get_launchpad()
        project = lp.projects[lp_project_name]

        # Incremental fetch: only retrieve bugs modified since last collection.
        # On the first run (no prior data) we fetch everything.
        last_fetched = await session.scalar(
            select(func.max(Issue.last_fetched_at))
            .where(Issue.project_id == project_id)
            .where(Issue.source == "launchpad")
        )

        search_kwargs: dict = {"status": list(_OPEN_STATUSES | _CLOSED_STATUSES)}
        if last_fetched is not None:
            search_kwargs["modified_since"] = last_fetched
            logger.info(
                "Incremental Launchpad fetch for %s since %s",
                lp_project_name,
                last_fetched,
            )
        else:
            logger.info("Full Launchpad fetch for %s (first run)", lp_project_name)

        bug_tasks = project.searchTasks(**search_kwargs)
        count = 0

        for task in bug_tasks:
            bug = task.bug
            state = _map_lp_status(task.status)
            author = (
                str(task.owner_link).rsplit("/", 1)[-1] if task.owner_link else None
            )
            author_is_maintainer = author in self._maintainers if author else False
            labels = list(bug.tags)

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
                content_hash=compute_content_hash(
                    bug.title, bug.description, state, labels
                ),
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
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        logger.info(
            "Collected %d bugs from Launchpad project %s", count, lp_project_name
        )
        return count
