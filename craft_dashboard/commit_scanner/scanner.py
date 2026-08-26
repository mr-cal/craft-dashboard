"""Commit scanner orchestration: turn new commits into evidence_generation bumps.

See plans/36-deep-evaluation-design.md section 4. The scanner is a
high-recall candidate generator, not a judge — a false positive costs one
wasted evaluation; a false negative leaves an issue permanently stale. It
therefore biases toward recall and uses no LLM.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from craft_dashboard.commit_scanner.parsing import extract_references
from craft_dashboard.git_mirrors import reader
from craft_dashboard.models.commit_scan_evidence_path import CommitScanEvidencePath
from craft_dashboard.models.commit_scan_run import CommitScanRun
from craft_dashboard.models.issue import Issue
from craft_dashboard.models.project import Project

if TYPE_CHECKING:
    import pathlib

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


async def scan_project(  # noqa: PLR0913
    session: AsyncSession,
    *,
    project_name: str,
    mirror_path: pathlib.Path,
    last_scanned_sha: str,
    new_head_sha: str,
    embed_client: EmbeddingClient | None,
    dry_run: bool,
    launchpad_projects: set[str] | None = None,
    semantic_top_k: int = 10,
    semantic_similarity_threshold: float = 0.70,
) -> CommitScanRun:
    """Scan commits between last_scanned_sha and new_head_sha, invalidate matches.

    Runs one `git log` over `<last_scanned_sha>..<new_head_sha>` (via the
    git_mirrors.reader helpers, with a NUL-delimited format — see
    _parse_log_output) and derives referenced issue numbers, changed paths,
    and (if embed_client is given) semantic candidates from the new
    commits' text. Applies **five** invalidation signals — qualified ref,
    bare ref, `LP: #N`, path intersection, and semantic match; each hit
    bumps the matched (OPEN) Issue.evidence_generation by 1 (unless
    dry_run).

    Args:
        session: Active async DB session. Caller is responsible for commit.
        project_name: The project whose mirror this is (matches Project.name).
        mirror_path: Path to the project's bare mirror.
        last_scanned_sha: Project.last_scanned_sha before this pass.
        new_head_sha: The mirror's current HEAD SHA (40-hex).
        embed_client: Used for semantic matching; pass None to skip that
            signal entirely (e.g. in tests that don't need it).
        dry_run: If True, computes and reports invalidation counts without
            writing anything (no evidence_generation bump, no commit).
        launchpad_projects: Project names with a Launchpad presence (from
            `launchpad-projects` in craft-dashboard.toml, currently
            {"snapcraft"}). An `LP: #N` ref only resolves when
            project_name is in this set. Defaults to empty.
        semantic_top_k: Tunable K for the semantic signal (design 36 §4).
            Operator-set via the CLI / settings; see scan_all_projects.
        semantic_similarity_threshold: Tunable similarity threshold for the
            semantic signal (design 36 §4). Operator-set via the CLI /
            settings; see scan_all_projects.

    Returns:
        The CommitScanRun row describing this pass. scan_project() always
        flushes this row and never commits; the caller is responsible for
        committing when dry_run is False and rolling back when dry_run is
        True (see scan_all_projects in Task 6).

    """
    started = time.monotonic()
    launchpad_projects = launchpad_projects or set()

    if last_scanned_sha == new_head_sha:
        commits_text = []
        changed_paths: list[str] = []
    else:
        reader.validate_ref(last_scanned_sha)
        reader.validate_ref(new_head_sha)
        # NUL-delimited, unambiguous format: per commit emit a record
        # separator, the message body (%B), then a field separator, then
        # the changed paths. `-z` NUL-terminates the --name-only paths so a
        # path can never be confused with message text (including
        # root-level files with no slash, or a bare URL line in the
        # message). `--no-merges` is set deliberately: `git log --name-only`
        # emits NO paths for merge commits by default, so a merge would
        # silently contribute an empty path set and defeat path
        # intersection. In the craft workflow PRs land as squash/rebase
        # commits (each a normal non-merge commit carrying the `#N`/paths),
        # so `--no-merges` loses no real signal while removing the
        # empty-path merge noise. If a repo ever relies on true merge
        # commits for landing, revisit with `--diff-merges=first-parent`.
        raw = await reader._run_git(  # noqa: SLF001 - internal helper reused intentionally
            mirror_path,
            "--no-pager",
            "log",
            "--no-merges",
            "--name-only",
            "-z",
            "--format=%x01%H%x00%B%x00",
            f"{last_scanned_sha}..{new_head_sha}",
        )
        commits_text, changed_paths = _parse_log_output(raw)

    invalidated_ids: dict[str, set[int]] = {
        "qualified_ref": set(),
        "path": set(),
        "semantic": set(),
        "bare_ref": set(),
        "launchpad": set(),
    }

    for message in commits_text:
        refs = extract_references(message)
        for ref in refs.qualified:
            issue_id = await find_issues_by_qualified_ref(
                session, project=ref.project, external_id=ref.external_id
            )
            if issue_id is not None:
                invalidated_ids["qualified_ref"].add(issue_id)
        for ref in refs.bare:
            issue_id = await find_issues_by_bare_ref(
                session, commit_project=project_name, external_id=ref.external_id
            )
            if issue_id is not None:
                invalidated_ids["bare_ref"].add(issue_id)
        for ref in refs.launchpad:
            issue_id = await find_issues_by_launchpad_ref(
                session,
                commit_project=project_name,
                external_id=ref.external_id,
                launchpad_projects=launchpad_projects,
            )
            if issue_id is not None:
                invalidated_ids["launchpad"].add(issue_id)

    if changed_paths:
        path_matches = await find_issues_by_changed_paths(
            session, project=project_name, changed_paths=changed_paths
        )
        invalidated_ids["path"] |= path_matches

    if embed_client is not None:
        for message in commits_text:
            semantic_matches = await find_issues_by_semantic_match(
                session,
                commit_text=message,
                embed_client=embed_client,
                top_k=semantic_top_k,
                similarity_threshold=semantic_similarity_threshold,
            )
            invalidated_ids["semantic"] |= semantic_matches

    if not dry_run:
        all_ids = set().union(*invalidated_ids.values())
        if all_ids:
            issues = (
                (
                    await session.execute(
                        select(Issue).where(
                            Issue.id.in_(all_ids),
                            Issue.state == "open",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for issue in issues:
                issue.evidence_generation += 1

        project_row = await session.scalar(
            select(Project).where(Project.name == project_name)
        )
        if project_row is not None:
            project_row.last_scanned_sha = new_head_sha

    run = CommitScanRun(
        project_id=(
            await session.scalar(select(Project.id).where(Project.name == project_name))
        ),
        scanned_at=datetime.now(tz=UTC),
        commits_scanned=len(commits_text),
        sha_before=last_scanned_sha,
        sha_after=new_head_sha,
        duration_seconds=time.monotonic() - started,
        invalidated_qualified_ref=len(invalidated_ids["qualified_ref"]),
        invalidated_path=len(invalidated_ids["path"]),
        invalidated_semantic=len(invalidated_ids["semantic"]),
        invalidated_bare_ref=len(invalidated_ids["bare_ref"]),
        invalidated_launchpad=len(invalidated_ids["launchpad"]),
        dry_run=dry_run,
    )
    session.add(run)
    await session.flush()
    return run


def _parse_log_output(raw: str) -> tuple[list[str], list[str]]:
    r"""Split the NUL-delimited `git log -z --name-only` output.

    The format string is `--format=%x01%H%x00%B%x00` with `-z`, so each
    commit record is:

        \\x01 <sha %H> \\x00 <message body %B> \\x00 <path>\\x00<path>\\x00 ...

    - `\\x01` (SOH) is the per-commit record separator (chosen because it
      cannot occur in a commit message or a path).
    - Within a record, splitting on `\\x00` yields
      `[sha, message, path1, path2, ...]` (a trailing empty element from the
      final `\\x00` is ignored).

    This is fully unambiguous: a root-level file with no slash
    (`README.md`, `pyproject.toml`), a path with spaces or unicode, and a
    bare URL sitting on its own line in the message are all classified
    correctly, because paths are delimited structurally rather than guessed
    from their shape. Returns (commit_messages, changed_paths); changed_paths
    is the deduplicated union across all commits in the range.
    """
    messages: list[str] = []
    paths: set[str] = set()

    # Records are separated by SOH (\x01); the leading empty chunk before
    # the first SOH is skipped.
    for record in raw.split("\x01"):
        if not record.strip("\x00\n "):
            continue
        parts = record.split("\x00")
        # parts[0] == commit sha, parts[1] == message, parts[2:] == paths.
        message = parts[1] if len(parts) > 1 else ""
        messages.append(message.strip())
        for path in parts[2:]:
            if path.strip():
                paths.add(path)

    return messages, sorted(paths)
