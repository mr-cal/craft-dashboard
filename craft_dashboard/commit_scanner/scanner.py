"""Commit scanner orchestration: turn new commits into evidence_generation bumps.

See plans/36-deep-evaluation-design.md section 4. The scanner is a
high-recall candidate generator, not a judge — a false positive costs one
wasted evaluation; a false negative leaves an issue permanently stale. It
therefore biases toward recall and uses no LLM.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from craft_dashboard.models.commit_scan_evidence_path import CommitScanEvidencePath
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from craft_dashboard.llm.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


async def find_issues_by_changed_paths(
    session: AsyncSession, *, project: str, changed_paths: list[str]
) -> set[int]:
    """Return OPEN issue IDs whose recorded evidence overlaps changed_paths.

    Scoped to (project, path) pairs — the same relative path string in a
    different repo never matches, since evidence paths are always recorded
    together with the project they were read from (see
    CommitScanEvidencePath). Joined to `issues` so only OPEN issues are
    returned: a changed path touching a closed issue's evidence must not
    bump its evidence_generation (design 36 §4).
    """
    if not changed_paths:
        return set()
    query = (
        select(CommitScanEvidencePath.issue_id)
        .join(Issue, Issue.id == CommitScanEvidencePath.issue_id)
        .where(
            CommitScanEvidencePath.project == project,
            CommitScanEvidencePath.path.in_(changed_paths),
            Issue.state == "open",
        )
    )
    result = await session.execute(query)
    return set(result.scalars().all())


async def find_issues_by_qualified_ref(
    session: AsyncSession, *, project: str, external_id: str
) -> int | None:
    """Resolve an exact cross-repo GitHub reference to an OPEN Issue.id, or None.

    Exact match: (Project.name == project) & (Issue.source == "github") &
    (Issue.external_id == external_id) & (Issue.state == "open"). The
    source filter is required because the unique constraint is
    (project_id, source, external_id): a project can hold a GitHub #42 and
    a Launchpad #42 at once, and a qualified GitHub ref must resolve only
    the GitHub row. The open filter enforces design 36 §4 (closed issues
    are never invalidated).
    """
    query = (
        select(Issue.id)
        .join(Project, Issue.project_id == Project.id)
        .where(
            Project.name == project,
            Issue.source == "github",
            Issue.external_id == external_id,
            Issue.state == "open",
        )
    )
    return await session.scalar(query)


async def find_issues_by_bare_ref(
    session: AsyncSession, *, commit_project: str, external_id: str
) -> int | None:
    """Resolve a bare #N reference within the commit's own repo only.

    Never matches a different project's issue with the same external_id —
    issue numbers are not globally unique across the 18 tracked repos, and
    a bare ref outside its own repo is only ever a weak signal, resolved
    later by the model via the issue_detail() tool (Phase 4), not by this
    function. Delegates to find_issues_by_qualified_ref, so it inherits the
    same github-source and open-state scoping.
    """
    return await find_issues_by_qualified_ref(
        session, project=commit_project, external_id=external_id
    )


async def find_issues_by_launchpad_ref(
    session: AsyncSession,
    *,
    commit_project: str,
    external_id: str,
    launchpad_projects: set[str],
) -> int | None:
    """Resolve an `LP: #N` reference to an OPEN Launchpad Issue.id, or None.

    `LP:` is cross-SOURCE, not cross-repo (design 36 §4): it names a
    Launchpad bug for the project whose repo the commit is in. So the ref
    resolves only when `commit_project` is one of the configured
    `launchpad-projects` (currently `{"snapcraft"}`), and only against a
    row with `source == "launchpad"` — never a GitHub issue that happens to
    share the number. Returns None (dropped) for commits in projects with
    no Launchpad presence.
    """
    if commit_project not in launchpad_projects:
        return None
    query = (
        select(Issue.id)
        .join(Project, Issue.project_id == Project.id)
        .where(
            Project.name == commit_project,
            Issue.source == "launchpad",
            Issue.external_id == external_id,
            Issue.state == "open",
        )
    )
    return await session.scalar(query)


async def find_issues_by_semantic_match(
    session: AsyncSession,
    *,
    commit_text: str,
    embed_client: EmbeddingClient,
    top_k: int,
    similarity_threshold: float,
) -> set[int]:
    """Return up to top_k OPEN issue IDs semantically close to commit_text."""
    embedding = await embed_client.embed(commit_text, dimensions=1024)
    distance_threshold = 1.0 - similarity_threshold
    distance = Issue.search_embedding.cosine_distance(embedding)

    query = (
        select(Issue.id, distance.label("distance"))
        .where(Issue.state == "open")
        .where(Issue.search_embedding.is_not(None))
        .where(distance < distance_threshold)
        .order_by(distance.asc())
        .limit(top_k)
    )

    result = await session.execute(query)
    return {row.id for row in result}
