"""Unit tests for craft_dashboard.commit_scanner.scanner."""

from __future__ import annotations

from craft_dashboard.commit_scanner.scanner import (
    find_issues_by_bare_ref,
    find_issues_by_changed_paths,
    find_issues_by_launchpad_ref,
    find_issues_by_qualified_ref,
)
from craft_dashboard.models.commit_scan_evidence_path import CommitScanEvidencePath

from tests.factories import make_issue, make_project


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


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
