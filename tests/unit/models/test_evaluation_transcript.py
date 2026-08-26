"""Unit tests for the EvaluationTranscript model."""

from __future__ import annotations

from craft_dashboard.models.evaluation_transcript import EvaluationTranscript

from tests.factories import make_evaluation, make_issue, make_project


async def _seed(session, *entities) -> None:
    session.add_all(entities)
    await session.commit()


class TestEvaluationTranscript:
    """Tests for the EvaluationTranscript model."""

    async def test_create_compact_transcript(self, test_db_session) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        evaluation = make_evaluation(id=1, issue_id=1)
        await _seed(test_db_session, project, issue, evaluation)

        transcript = EvaluationTranscript(
            llm_evaluation_id=1,
            rounds=[
                {
                    "tool_name": "grep_repo",
                    "arguments": {
                        "pattern": "is not a valid part name",
                        "repos": ["craft-parts"],
                    },
                    "result_sha256": "abc123",
                    "result_byte_count": 842,
                    "result_preview": "craft_parts/parts.py:41: raise ...",
                    "reasoning": "Looking for the error string from the traceback.",
                }
            ],
            full_capture=False,
            model_name="z-ai/glm-5.2",
            rounds_used=1,
        )
        test_db_session.add(transcript)
        await test_db_session.commit()

        assert transcript.id is not None
        assert transcript.rounds[0]["tool_name"] == "grep_repo"
        assert transcript.full_capture is False

    async def test_defaults_to_compact_capture(self, test_db_session) -> None:
        project = make_project(id=1, name="snapcraft")
        issue = make_issue(id=1, project_id=1, external_id="1")
        evaluation = make_evaluation(id=1, issue_id=1)
        await _seed(test_db_session, project, issue, evaluation)

        transcript = EvaluationTranscript(
            llm_evaluation_id=1, rounds=[], model_name="test-model", rounds_used=0
        )
        test_db_session.add(transcript)
        await test_db_session.commit()

        assert transcript.full_capture is False
