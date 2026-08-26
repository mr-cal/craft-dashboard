"""Unit tests for IssueLinkRepository."""

from __future__ import annotations

from craft_dashboard.models.issue_link import IssueLink
from craft_dashboard.repositories.issue_link_repository import IssueLinkRepository
from sqlalchemy import select

from tests.factories import make_evaluation, make_issue, make_project


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


class TestCreateFromDuplicateCheck:
    """Tests for persisting DuplicateDetector.check_duplicates() output."""

    async def test_confirmed_duplicate_is_persisted(self, test_db_session) -> None:
        project = make_project(id=1, name="rockcraft")
        from_issue = make_issue(id=1, project_id=1, external_id="100")
        to_issue = make_issue(id=2, project_id=1, external_id="200")
        evaluation = make_evaluation(id=1, issue_id=1)
        await _seed(test_db_session, project, from_issue, to_issue, evaluation)

        repo = IssueLinkRepository(test_db_session)
        duplicate_result = {
            "duplicate_of_issue_id": 2,
            "duplicate_of_external_id": "200",
            "duplicate_of_project_name": "rockcraft",
            "confidence": 85,
            "reason": "Both describe the same crash in the pull step.",
            "candidates_compared": 3,
        }

        link = await repo.create_from_duplicate_check(
            from_issue_id=1,
            llm_evaluation_id=1,
            duplicate_result=duplicate_result,
        )
        await test_db_session.commit()

        assert link is not None
        assert link.kind == "duplicate_of"
        assert link.source == "duplicate_detector"
        assert link.to_issue_id == 2
        assert link.to_ref == "rockcraft#200"
        assert link.confidence == 85

        stored = (
            await test_db_session.execute(
                select(IssueLink).where(IssueLink.id == link.id)
            )
        ).scalar_one()
        assert stored.note == "Both describe the same crash in the pull step."

    async def test_no_duplicate_found_persists_nothing(self, test_db_session) -> None:
        project = make_project(id=1, name="rockcraft")
        from_issue = make_issue(id=1, project_id=1, external_id="100")
        evaluation = make_evaluation(id=1, issue_id=1)
        await _seed(test_db_session, project, from_issue, evaluation)

        repo = IssueLinkRepository(test_db_session)
        # Shape returned by check_duplicates() when no candidate was confirmed.
        duplicate_result = {"candidates_compared": 5}

        link = await repo.create_from_duplicate_check(
            from_issue_id=1,
            llm_evaluation_id=1,
            duplicate_result=duplicate_result,
        )

        assert link is None
        count = (await test_db_session.execute(select(IssueLink))).scalars().all()
        assert count == []


class TestGetLatestLinksForIssue:
    """Tests for reading only links from an issue's current evaluation."""

    async def test_only_latest_evaluations_links_are_returned(
        self, test_db_session
    ) -> None:
        project = make_project(id=1, name="rockcraft")
        from_issue = make_issue(id=1, project_id=1, external_id="100")
        to_issue = make_issue(id=2, project_id=1, external_id="200")
        old_evaluation = make_evaluation(id=1, issue_id=1, latest=False)
        new_evaluation = make_evaluation(id=2, issue_id=1, latest=True)
        await _seed(
            test_db_session,
            project,
            from_issue,
            to_issue,
            old_evaluation,
            new_evaluation,
        )

        repo = IssueLinkRepository(test_db_session)
        stale_link = IssueLink(
            from_issue_id=1,
            llm_evaluation_id=1,
            to_issue_id=2,
            to_ref="rockcraft#200",
            kind="related_to",
            confidence=50,
            source="evaluator",
        )
        current_link = IssueLink(
            from_issue_id=1,
            llm_evaluation_id=2,
            to_issue_id=2,
            to_ref="rockcraft#200",
            kind="likely_fixed_by",
            confidence=80,
            source="evaluator",
        )
        test_db_session.add_all([stale_link, current_link])
        await test_db_session.commit()

        links = await repo.get_latest_links_for_issue(1)

        assert len(links) == 1
        assert links[0].kind == "likely_fixed_by"
