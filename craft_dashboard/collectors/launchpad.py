"""Launchpad data collector for bugs."""

import logging
from datetime import UTC, datetime

import sqlalchemy as sa

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

    def __init__(self, projects: list[str] | None = None) -> None:
        """Initialize the Launchpad collector.

        Args:
            projects: List of Launchpad project names to collect from.

        """
        self.projects = projects or []
        self._lp = None

    def _get_launchpad(self):  # noqa: ANN202
        """Lazily initialize the Launchpad API client.

        Returns:
            A launchpadlib Launchpad instance.

        """
        if self._lp is None:
            from launchpadlib.launchpad import Launchpad  # noqa: PLC0415

            self._lp = Launchpad.login_anonymously(
                "craft-dashboard", "production", version="devel"
            )
        return self._lp

    async def collect_bugs(
        self,
        lp_project_name: str,
        project_id: int,
        session,  # noqa: ANN001
    ) -> int:
        """Collect bugs for a Launchpad project.

        Args:
            lp_project_name: Launchpad project name.
            project_id: The database ID of the project.
            session: An async SQLAlchemy session.

        Returns:
            The number of bugs upserted.

        """
        from sqlalchemy.dialects.postgresql import insert  # noqa: PLC0415

        from craft_dashboard.models.issue import Issue  # noqa: PLC0415

        lp = self._get_launchpad()
        project = lp.projects[lp_project_name]
        bug_tasks = project.searchTasks(status=list(_OPEN_STATUSES | _CLOSED_STATUSES))
        count = 0

        for task in bug_tasks:
            bug = task.bug
            state = _map_lp_status(task.status)

            stmt = insert(Issue).values(
                project_id=project_id,
                source="launchpad",
                external_id=str(bug.id),
                issue_type="issue",
                title=bug.title,
                body=bug.description,
                state=state,
                author=str(task.owner_link).rsplit("/", 1)[-1]
                if task.owner_link
                else None,
                author_is_maintainer=False,
                labels=list(bug.tags),
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
                last_fetched_at=datetime.now(tz=UTC),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["project_id", "source", "external_id"],
                set_={
                    "title": stmt.excluded.title,
                    "body": stmt.excluded.body,
                    "state": stmt.excluded.state,
                    "labels": stmt.excluded.labels,
                    "updated_at": stmt.excluded.updated_at,
                    "closed_at": stmt.excluded.closed_at,
                    "last_fetched_at": stmt.excluded.last_fetched_at,
                    "metadata": sa.literal_column("excluded.metadata"),
                },
            )
            await session.execute(stmt)
            count += 1

        await session.commit()
        logger.info(
            "Collected %d bugs from Launchpad project %s", count, lp_project_name
        )
        return count
