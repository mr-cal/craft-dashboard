"""Unit tests for craft_dashboard.commit_scanner.scanner."""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from craft_dashboard.commit_scanner.scanner import (
    _parse_log_output,
    find_issues_by_bare_ref,
    find_issues_by_changed_paths,
    find_issues_by_launchpad_ref,
    find_issues_by_qualified_ref,
    find_issues_by_semantic_match,
    scan_project,
)
from craft_dashboard.models.commit_scan_evidence_path import CommitScanEvidencePath

from tests.factories import make_issue, make_project
from tests.unit.commit_scanner.conftest import commit

if TYPE_CHECKING:
    import pathlib


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


def _run_git_stdout(*args: str, cwd: pathlib.Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _clone_mirror(repo: pathlib.Path, mirror: pathlib.Path) -> None:
    subprocess.run(
        ["git", "clone", "--mirror", "-q", str(repo), str(mirror)],
        check=True,
        capture_output=True,
    )


class TestFindIssuesByChangedPaths:
    """Path-intersection invalidation: (project, path) reverse-index lookup."""

    async def test_matches_issue_whose_evidence_touched_the_path(
        self, test_db_session
    ) -> None:
        project = make_project(id=1, name="craft-parts")
        issue = make_issue(id=1, project_id=1, external_id="1", state="open")
        await _seed(test_db_session, project, issue)
        evidence = CommitScanEvidencePath(
            issue_id=1,
            project="craft-parts",
            path="craft_parts/executor/step_handler.py",
        )
        await _seed(test_db_session, evidence)

        matches = await find_issues_by_changed_paths(
            test_db_session,
            project="craft-parts",
            changed_paths=["craft_parts/executor/step_handler.py", "README.md"],
        )

        assert matches == {1}

    async def test_no_match_for_untouched_path(self, test_db_session) -> None:
        project = make_project(id=1, name="craft-parts")
        issue = make_issue(id=1, project_id=1, external_id="1", state="open")
        await _seed(test_db_session, project, issue)
        evidence = CommitScanEvidencePath(
            issue_id=1,
            project="craft-parts",
            path="craft_parts/executor/step_handler.py",
        )
        await _seed(test_db_session, evidence)

        matches = await find_issues_by_changed_paths(
            test_db_session, project="craft-parts", changed_paths=["README.md"]
        )

        assert matches == set()

    async def test_scoped_to_project_even_with_same_path_string(
        self, test_db_session
    ) -> None:
        """The same relative path in two different repos must not cross-match."""
        project_a = make_project(id=1, name="craft-parts")
        project_b = make_project(id=2, name="rockcraft", github_org="canonical")
        issue_a = make_issue(id=1, project_id=1, external_id="1", state="open")
        issue_b = make_issue(id=2, project_id=2, external_id="2", state="open")
        await _seed(test_db_session, project_a, project_b, issue_a, issue_b)
        evidence = CommitScanEvidencePath(
            issue_id=2, project="rockcraft", path="src/main.py"
        )
        await _seed(test_db_session, evidence)

        matches = await find_issues_by_changed_paths(
            test_db_session, project="craft-parts", changed_paths=["src/main.py"]
        )

        assert matches == set()

    async def test_closed_issue_is_not_matched(self, test_db_session) -> None:
        """Path intersection only invalidates OPEN issues (design 36 §4)."""
        project = make_project(id=1, name="craft-parts")
        issue = make_issue(id=1, project_id=1, external_id="1", state="closed")
        await _seed(test_db_session, project, issue)
        evidence = CommitScanEvidencePath(
            issue_id=1,
            project="craft-parts",
            path="craft_parts/executor/step_handler.py",
        )
        await _seed(test_db_session, evidence)

        matches = await find_issues_by_changed_paths(
            test_db_session,
            project="craft-parts",
            changed_paths=["craft_parts/executor/step_handler.py"],
        )

        assert matches == set()  # closed -> never bumped


class TestFindIssuesByQualifiedRef:
    """Exact cross-repo match: (project_name, external_id) -> Issue.id."""

    async def test_resolves_to_issue_id(self, test_db_session) -> None:
        project = make_project(id=1, name="craft-parts")
        issue = make_issue(id=1, project_id=1, external_id="567", state="open")
        await _seed(test_db_session, project, issue)

        issue_id = await find_issues_by_qualified_ref(
            test_db_session, project="craft-parts", external_id="567"
        )

        assert issue_id == 1

    async def test_unresolvable_ref_returns_none(self, test_db_session) -> None:
        issue_id = await find_issues_by_qualified_ref(
            test_db_session, project="craft-parts", external_id="99999"
        )
        assert issue_id is None

    async def test_closed_issue_is_not_resolved(self, test_db_session) -> None:
        """Invalidation only ever touches OPEN issues (design 36 §4)."""
        project = make_project(id=1, name="craft-parts")
        issue = make_issue(id=1, project_id=1, external_id="567", state="closed")
        await _seed(test_db_session, project, issue)

        issue_id = await find_issues_by_qualified_ref(
            test_db_session, project="craft-parts", external_id="567"
        )

        assert issue_id is None  # closed -> never bumped

    async def test_source_disambiguates_same_external_id(self, test_db_session) -> None:
        """A GitHub qualified ref must not resolve a Launchpad issue with the
        same external_id (unique constraint is (project_id, source, external_id))."""
        project = make_project(id=1, name="snapcraft")
        gh = make_issue(
            id=1, project_id=1, source="github", external_id="42", state="open"
        )
        lp = make_issue(
            id=2, project_id=1, source="launchpad", external_id="42", state="open"
        )
        await _seed(test_db_session, project, gh, lp)

        issue_id = await find_issues_by_qualified_ref(
            test_db_session, project="snapcraft", external_id="42"
        )

        assert issue_id == 1  # the github row, never the launchpad row


class TestFindIssuesByBareRef:
    """Bare #N: repo-scoped only — must never match a different project's issue."""

    async def test_matches_only_within_same_project(self, test_db_session) -> None:
        project_a = make_project(id=1, name="craft-parts")
        project_b = make_project(id=2, name="rockcraft", github_org="canonical")
        issue_a = make_issue(id=1, project_id=1, external_id="42", state="open")
        issue_b = make_issue(id=2, project_id=2, external_id="42", state="open")
        await _seed(test_db_session, project_a, project_b, issue_a, issue_b)

        issue_id = await find_issues_by_bare_ref(
            test_db_session, commit_project="craft-parts", external_id="42"
        )

        assert issue_id == 1  # never issue_b, even though its external_id also matches

    async def test_closed_issue_is_not_resolved(self, test_db_session) -> None:
        project = make_project(id=1, name="craft-parts")
        issue = make_issue(id=1, project_id=1, external_id="42", state="closed")
        await _seed(test_db_session, project, issue)

        issue_id = await find_issues_by_bare_ref(
            test_db_session, commit_project="craft-parts", external_id="42"
        )

        assert issue_id is None  # closed -> never bumped


class TestFindIssuesByLaunchpadRef:
    """LP: #N is cross-SOURCE: resolves against launchpad-projects only."""

    async def test_resolves_launchpad_issue_in_launchpad_project(
        self, test_db_session
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1, project_id=1, source="launchpad", external_id="2012345", state="open"
        )
        await _seed(test_db_session, project, issue)

        issue_id = await find_issues_by_launchpad_ref(
            test_db_session,
            commit_project="snapcraft",
            external_id="2012345",
            launchpad_projects={"snapcraft"},
        )

        assert issue_id == 1

    async def test_ref_in_non_launchpad_project_resolves_to_none(
        self, test_db_session
    ) -> None:
        """An LP: ref in a repo not listed in launchpad-projects is dropped."""
        project = make_project(id=1, name="craft-parts")
        await _seed(test_db_session, project)

        issue_id = await find_issues_by_launchpad_ref(
            test_db_session,
            commit_project="craft-parts",
            external_id="2012345",
            launchpad_projects={"snapcraft"},
        )

        assert issue_id is None

    async def test_never_resolves_a_github_issue(self, test_db_session) -> None:
        """A Launchpad ref must not match a GitHub issue with the same number."""
        project = make_project(id=1, name="snapcraft")
        gh = make_issue(
            id=1, project_id=1, source="github", external_id="2012345", state="open"
        )
        await _seed(test_db_session, project, gh)

        issue_id = await find_issues_by_launchpad_ref(
            test_db_session,
            commit_project="snapcraft",
            external_id="2012345",
            launchpad_projects={"snapcraft"},
        )

        assert issue_id is None

    async def test_closed_launchpad_issue_is_not_resolved(
        self, test_db_session
    ) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(
            id=1,
            project_id=1,
            source="launchpad",
            external_id="2012345",
            state="closed",
        )
        await _seed(test_db_session, project, issue)

        issue_id = await find_issues_by_launchpad_ref(
            test_db_session,
            commit_project="snapcraft",
            external_id="2012345",
            launchpad_projects={"snapcraft"},
        )

        assert issue_id is None


class TestFindIssuesBySemanticMatch:
    """Semantic candidate generation: cosine search over Issue.search_embedding."""

    async def test_returns_candidates_above_threshold(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=iter([SimpleNamespace(id=1), SimpleNamespace(id=2)])
        )
        embed_client = AsyncMock()
        embed_client.embed = AsyncMock(return_value=[0.1] * 1024)

        candidates = await find_issues_by_semantic_match(
            session,
            commit_text="Fix crash in the pull step handler",
            embed_client=embed_client,
            top_k=5,
            similarity_threshold=0.5,
        )

        assert candidates == {1, 2}
        embed_client.embed.assert_awaited_once_with(
            "Fix crash in the pull step handler", dimensions=1024
        )

    async def test_below_threshold_is_excluded(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=iter([]))
        embed_client = AsyncMock()
        embed_client.embed = AsyncMock(return_value=[-0.1] * 512 + [0.1] * 512)

        candidates = await find_issues_by_semantic_match(
            session,
            commit_text="Totally unrelated commit about docs typo",
            embed_client=embed_client,
            top_k=5,
            similarity_threshold=0.9,
        )

        (query,) = session.execute.await_args.args
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

        assert candidates == set()
        assert "issues.state = 'open'" in compiled
        assert "issues.search_embedding IS NOT NULL" in compiled
        assert "0.09999999" in compiled or "0.1" in compiled

    async def test_respects_top_k(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=iter([SimpleNamespace(id=1), SimpleNamespace(id=2)])
        )
        embed_client = AsyncMock()
        embed_client.embed = AsyncMock(return_value=[0.1] * 1024)

        candidates = await find_issues_by_semantic_match(
            session,
            commit_text="Fix crash",
            embed_client=embed_client,
            top_k=2,
            similarity_threshold=0.5,
        )

        (query,) = session.execute.await_args.args
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

        assert candidates == {1, 2}
        assert "LIMIT 2" in compiled


class TestScanProject:
    """End-to-end (git + DB, no LLM) test of one scanner pass."""

    async def test_qualified_ref_bumps_evidence_generation(
        self, test_db_session, scanner_repo, tmp_path
    ) -> None:
        target_project = make_project(id=1, name="craft-parts")
        target_issue = make_issue(
            id=1, project_id=1, external_id="567", state="open", evidence_generation=0
        )
        await _seed(test_db_session, target_project, target_issue)

        base_sha = await asyncio.to_thread(
            _run_git_stdout,
            "rev-parse",
            "HEAD",
            cwd=scanner_repo,
        )
        new_sha = await asyncio.to_thread(
            commit,
            scanner_repo,
            filename="fix.py",
            content="fixed = True\n",
            message="Fix the crash (canonical/craft-parts#567)",
        )

        mirror = tmp_path / "mirror.git"
        await asyncio.to_thread(
            _clone_mirror,
            scanner_repo,
            mirror,
        )

        run = await scan_project(
            test_db_session,
            project_name="craft-parts",
            mirror_path=mirror,
            last_scanned_sha=base_sha,
            new_head_sha=new_sha,
            embed_client=None,  # no semantic candidates needed for this test
            dry_run=False,
        )

        await test_db_session.refresh(target_issue)
        assert target_issue.evidence_generation == 1
        assert run.invalidated_qualified_ref == 1
        assert run.commits_scanned == 1
        assert run.sha_after == new_sha

    async def test_dry_run_does_not_mutate_evidence_generation(
        self, test_db_session, scanner_repo, tmp_path
    ) -> None:
        target_project = make_project(id=1, name="craft-parts")
        target_issue = make_issue(
            id=1, project_id=1, external_id="567", state="open", evidence_generation=0
        )
        await _seed(test_db_session, target_project, target_issue)

        base_sha = await asyncio.to_thread(
            _run_git_stdout,
            "rev-parse",
            "HEAD",
            cwd=scanner_repo,
        )
        new_sha = await asyncio.to_thread(
            commit,
            scanner_repo,
            filename="fix.py",
            content="x\n",
            message="Fix the crash (canonical/craft-parts#567)",
        )
        mirror = tmp_path / "mirror.git"
        await asyncio.to_thread(
            _clone_mirror,
            scanner_repo,
            mirror,
        )

        run = await scan_project(
            test_db_session,
            project_name="craft-parts",
            mirror_path=mirror,
            last_scanned_sha=base_sha,
            new_head_sha=new_sha,
            embed_client=None,
            dry_run=True,
        )

        await test_db_session.refresh(target_issue)
        assert target_issue.evidence_generation == 0  # unchanged — dry run
        assert run.dry_run is True
        assert run.invalidated_qualified_ref == 1  # still reported, just not applied

    async def test_launchpad_ref_bumps_evidence_generation(
        self, test_db_session, scanner_repo, tmp_path
    ) -> None:
        """An `LP: #N` commit in a launchpad-project repo bumps the LP issue."""
        target_project = make_project(id=1, name="snapcraft")
        target_issue = make_issue(
            id=1,
            project_id=1,
            source="launchpad",
            external_id="2012345",
            state="open",
            evidence_generation=0,
        )
        await _seed(test_db_session, target_project, target_issue)

        base_sha = await asyncio.to_thread(
            _run_git_stdout,
            "rev-parse",
            "HEAD",
            cwd=scanner_repo,
        )
        new_sha = await asyncio.to_thread(
            commit,
            scanner_repo,
            filename="fix.py",
            content="x\n",
            message="Fix confinement (LP: #2012345)",
        )
        mirror = tmp_path / "mirror.git"
        await asyncio.to_thread(
            _clone_mirror,
            scanner_repo,
            mirror,
        )

        run = await scan_project(
            test_db_session,
            project_name="snapcraft",
            mirror_path=mirror,
            last_scanned_sha=base_sha,
            new_head_sha=new_sha,
            embed_client=None,
            launchpad_projects={"snapcraft"},
            dry_run=False,
        )

        await test_db_session.refresh(target_issue)
        assert target_issue.evidence_generation == 1
        assert run.invalidated_launchpad == 1

    async def test_does_not_bump_issue_closed_after_match_resolution(
        self, test_db_session, scanner_repo, tmp_path, monkeypatch
    ) -> None:
        target_project = make_project(id=1, name="craft-parts")
        target_issue = make_issue(
            id=1, project_id=1, external_id="567", state="open", evidence_generation=0
        )
        await _seed(test_db_session, target_project, target_issue)

        base_sha = await asyncio.to_thread(
            _run_git_stdout,
            "rev-parse",
            "HEAD",
            cwd=scanner_repo,
        )
        new_sha = await asyncio.to_thread(
            commit,
            scanner_repo,
            filename="fix.py",
            content="fixed = True\n",
            message="Fix the crash (canonical/craft-parts#567)",
        )

        mirror = tmp_path / "mirror.git"
        await asyncio.to_thread(_clone_mirror, scanner_repo, mirror)

        async def close_issue_after_match(
            session, *, project: str, external_id: str
        ) -> int | None:
            assert project == "craft-parts"
            assert external_id == "567"
            target_issue.state = "closed"
            await session.flush()
            return target_issue.id

        monkeypatch.setattr(
            "craft_dashboard.commit_scanner.scanner.find_issues_by_qualified_ref",
            close_issue_after_match,
        )

        run = await scan_project(
            test_db_session,
            project_name="craft-parts",
            mirror_path=mirror,
            last_scanned_sha=base_sha,
            new_head_sha=new_sha,
            embed_client=None,
            dry_run=False,
        )

        await test_db_session.refresh(target_issue)
        assert target_issue.state == "closed"
        assert target_issue.evidence_generation == 0
        assert run.invalidated_qualified_ref == 1


class TestParseLogOutput:
    """Direct coverage of _parse_log_output — the message/path split that the
    end-to-end tests above exercise only via the message signal."""

    def test_nested_and_root_paths_are_both_captured(self) -> None:
        # One record: \x01 <sha> \x00 <message> \x00 <path>\x00... (see the
        # --format=%x01%H%x00%B%x00 -z command in scan_project).
        raw = (
            "\x01abc123\x00Fix the thing\n\nDetails here\x00"
            "craft_parts/executor/step_handler.py\x00"
            "README.md\x00"  # root-level file MUST be captured
            "pyproject.toml\x00"
        )
        messages, paths = _parse_log_output(raw)

        assert "Fix the thing\n\nDetails here" in messages[0]
        # Root-level files (no slash) are real changed paths, not message text.
        assert "README.md" in paths
        assert "pyproject.toml" in paths
        assert "craft_parts/executor/step_handler.py" in paths

    def test_url_only_line_stays_in_message_not_paths(self) -> None:
        raw = (
            "\x01abc123\x00See https://github.com/canonical/craft-parts/issues/567\x00"
            "src/fix.py\x00"
        )
        messages, paths = _parse_log_output(raw)

        # The URL must remain in the message so extract_references can see the
        # qualified-URL ref; it must NOT be misclassified as a changed path.
        assert "https://github.com/canonical/craft-parts/issues/567" in messages[0]
        assert paths == ["src/fix.py"]

    def test_preserves_significant_path_whitespace(self) -> None:
        raw = (
            "\x01abc123\x00Fix whitespace-sensitive path handling\x00"
            "  path/with-space.py  \x00"
            "   \x00"
        )
        _messages, paths = _parse_log_output(raw)

        assert paths == ["  path/with-space.py  "]
