"""Unit tests for the IssueLink model."""

from __future__ import annotations

from craft_dashboard.models.issue_link import IssueLink

from tests.factories import make_evaluation, make_issue, make_project


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


class TestIssueLink:
    """Tests for the IssueLink model and its table constraints."""

    async def test_create_link_with_resolved_target(self, test_db_session) -> None:
        project = make_project(id=1, name="rockcraft")
        from_issue = make_issue(id=1, project_id=1, external_id="100")
        to_issue = make_issue(id=2, project_id=1, external_id="200")
        evaluation = make_evaluation(id=1, issue_id=1)
        await _seed(test_db_session, project, from_issue, to_issue, evaluation)

        link = IssueLink(
            from_issue_id=1,
            llm_evaluation_id=1,
            to_issue_id=2,
            to_ref="canonical/craft-parts#567",
            kind="likely_fixed_by",
            confidence=75,
            note="Merged 2026-03-11; rewrites the pull-step handler.",
            source="evaluator",
        )
        test_db_session.add(link)
        await test_db_session.commit()

        assert link.id is not None
        assert link.to_issue_id == 2
        assert link.kind == "likely_fixed_by"

    async def test_create_link_with_unresolved_target(self, test_db_session) -> None:
        """to_issue_id is nullable — an unresolvable ref is still persisted."""
        project = make_project(id=1, name="rockcraft")
        from_issue = make_issue(id=1, project_id=1, external_id="100")
        evaluation = make_evaluation(id=1, issue_id=1)
        await _seed(test_db_session, project, from_issue, evaluation)

        link = IssueLink(
            from_issue_id=1,
            llm_evaluation_id=1,
            to_issue_id=None,
            to_ref="canonical/some-unknown-repo#9999",
            kind="related_to",
            confidence=40,
            note="Reference could not be resolved to a known issue.",
            source="evaluator",
        )
        test_db_session.add(link)
        await test_db_session.commit()

        assert link.id is not None
        assert link.to_issue_id is None
        assert link.to_ref == "canonical/some-unknown-repo#9999"

    async def test_repr_includes_kind_and_ref(self, test_db_session) -> None:
        project = make_project(id=1, name="rockcraft")
        from_issue = make_issue(id=1, project_id=1, external_id="100")
        evaluation = make_evaluation(id=1, issue_id=1)
        await _seed(test_db_session, project, from_issue, evaluation)

        link = IssueLink(
            from_issue_id=1,
            llm_evaluation_id=1,
            to_ref="canonical/craft-parts#567",
            kind="duplicate_of",
            confidence=90,
            source="duplicate_detector",
        )
        test_db_session.add(link)
        await test_db_session.commit()

        assert "duplicate_of" in repr(link)
        assert "canonical/craft-parts#567" in repr(link)
