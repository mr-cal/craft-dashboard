"""Unit tests for scripts.llm.bakeoff.scoring_pilot."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from craft_dashboard.git_mirrors.exceptions import UnknownProjectError
from craft_dashboard.llm.client import LLMResponse
from scripts.llm.bakeoff.common import BakeoffResult
from scripts.llm.bakeoff.scoring_pilot import (
    _pinned_sha_for_project,
    run_scoring_pilot,
)

from tests.factories import make_issue, make_project


def _final(
    content: str = '{"scores": {"impact": 70}, "related_work": []}',
) -> LLMResponse:
    return LLMResponse(
        content=content,
        prompt_tokens=200,
        completion_tokens=40,
        total_tokens=240,
        model="model-a",
        cost_usd=0.002,
        tool_calls=None,
    )


def _tool_call() -> LLMResponse:
    return LLMResponse(
        content="",
        prompt_tokens=200,
        completion_tokens=20,
        total_tokens=220,
        model="model-a",
        cost_usd=0.001,
        tool_calls=[
            {
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "repo_layout",
                    "arguments": '{"project": "craft-parts"}',
                },
            }
        ],
    )


TEST_EVAL_API_TOKEN = "test-eval-api-token"


async def _seed(session) -> None:
    session.add_all(
        [
            make_project(id=1, name="craft-parts"),
            make_issue(
                id=1,
                project_id=1,
                source="github",
                external_id="1",
                title="Bug",
            ),
        ]
    )
    await session.commit()


async def _seed_two_projects(session) -> None:
    session.add_all(
        [
            make_project(id=1, name="craft-parts"),
            make_project(id=2, name="rockcraft"),
            make_issue(
                id=1,
                project_id=1,
                source="github",
                external_id="1",
                title="Bug A",
            ),
            make_issue(
                id=2,
                project_id=2,
                source="github",
                external_id="2",
                title="Bug B",
            ),
        ]
    )
    await session.commit()


class TestRunScoringPilot:
    async def test_no_tool_calls_stops_after_one_round(
        self, test_db_session, tmp_path
    ) -> None:
        await _seed(test_db_session)
        sample = tmp_path / "s.json"
        sample.write_text(
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
        )
        client = AsyncMock()
        client.complete.return_value = _final()
        with (
            patch("scripts.llm.bakeoff.scoring_pilot.make_client", return_value=client),
            patch(
                "scripts.llm.bakeoff.scoring_pilot.build_round1_baseline",
                return_value="B",
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._pinned_sha_for_project",
                return_value="a" * 40,
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._allowed_projects",
                new=AsyncMock(return_value={"craft-parts": "canonical"}),
            ),
        ):
            results = await run_scoring_pilot(
                session=test_db_session,
                models=["model-a"],
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=tmp_path / "t",
                mirror_dir=tmp_path / "m",
                api_key="k",
                max_rounds=6,
            )
        assert client.complete.call_count == 1
        assert results[0].rounds_used == 1
        assert results[0].completed is True
        assert results[0].new_output["scores"]["impact"] == 70

    async def test_dispatches_tools_and_continues(
        self, test_db_session, tmp_path
    ) -> None:
        await _seed(test_db_session)
        sample = tmp_path / "s.json"
        sample.write_text(
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
        )
        client = AsyncMock()
        client.complete.side_effect = [_tool_call(), _final()]
        with (
            patch("scripts.llm.bakeoff.scoring_pilot.make_client", return_value=client),
            patch(
                "scripts.llm.bakeoff.scoring_pilot.build_round1_baseline",
                return_value="B",
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot.dispatch_tool_call",
                new=AsyncMock(return_value="src/\t3 files"),
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._pinned_sha_for_project",
                return_value="a" * 40,
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._allowed_projects",
                new=AsyncMock(return_value={"craft-parts": "canonical"}),
            ),
        ):
            results = await run_scoring_pilot(
                session=test_db_session,
                models=["model-a"],
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=tmp_path / "t",
                mirror_dir=tmp_path / "m",
                api_key="k",
                max_rounds=6,
            )
        assert client.complete.call_count == 2
        assert results[0].rounds_used == 2
        assert results[0].tools_called == ["repo_layout"]
        assert results[0].cost_usd == pytest.approx(0.003)

    async def test_hits_max_rounds_and_records_incomplete(
        self, test_db_session, tmp_path
    ) -> None:
        await _seed(test_db_session)
        sample = tmp_path / "s.json"
        sample.write_text(
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
        )
        client = AsyncMock()
        client.complete.return_value = _tool_call()
        with (
            patch("scripts.llm.bakeoff.scoring_pilot.make_client", return_value=client),
            patch(
                "scripts.llm.bakeoff.scoring_pilot.build_round1_baseline",
                return_value="B",
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot.dispatch_tool_call",
                new=AsyncMock(return_value="x"),
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._pinned_sha_for_project",
                return_value="a" * 40,
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._allowed_projects",
                new=AsyncMock(return_value={"craft-parts": "canonical"}),
            ),
        ):
            results = await run_scoring_pilot(
                session=test_db_session,
                models=["model-a"],
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=tmp_path / "t",
                mirror_dir=tmp_path / "m",
                api_key="k",
                max_rounds=3,
            )
        assert client.complete.call_count == 3
        assert results[0].rounds_used == 3
        assert results[0].completed is False

    async def test_model_failure_is_isolated_not_fatal(
        self, test_db_session, tmp_path
    ) -> None:
        await _seed(test_db_session)
        sample = tmp_path / "s.json"
        sample.write_text(
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
        )
        client = AsyncMock()
        client.complete.side_effect = RuntimeError("rate limited")
        with (
            patch("scripts.llm.bakeoff.scoring_pilot.make_client", return_value=client),
            patch(
                "scripts.llm.bakeoff.scoring_pilot.build_round1_baseline",
                return_value="B",
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._pinned_sha_for_project",
                return_value="a" * 40,
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._allowed_projects",
                new=AsyncMock(return_value={"craft-parts": "canonical"}),
            ),
        ):
            results = await run_scoring_pilot(
                session=test_db_session,
                models=["model-a"],
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=tmp_path / "t",
                mirror_dir=tmp_path / "m",
                api_key="k",
                max_rounds=6,
            )
        assert results[0].error is not None
        assert "rate limited" in results[0].error

    async def test_isolates_missing_mirror_to_one_entry(
        self, test_db_session, tmp_path
    ) -> None:
        await _seed_two_projects(test_db_session)
        sample = tmp_path / "s.json"
        sample.write_text(
            "[{\"source\":\"github\",\"project\":\"craft-parts\",\"external_id\":\"1\"},"
            "{\"source\":\"github\",\"project\":\"rockcraft\",\"external_id\":\"2\"}]"
        )
        client = AsyncMock()
        client.complete.return_value = _final()
        with (
            patch("scripts.llm.bakeoff.scoring_pilot.make_client", return_value=client),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._load_pinned_shas",
                return_value=(
                    {"rockcraft": "b" * 40},
                    {"craft-parts": "missing mirror"},
                ),
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._allowed_projects",
                new=AsyncMock(
                    return_value={
                        "craft-parts": "canonical",
                        "rockcraft": "canonical",
                    }
                ),
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._load_related_baseline",
                new=AsyncMock(return_value=[]),
            ),
        ):
            results = await run_scoring_pilot(
                session=test_db_session,
                models=["model-a"],
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=tmp_path / "t",
                mirror_dir=tmp_path / "m",
                api_key="k",
                eval_server_base_url="https://eval.example",
                eval_api_token=TEST_EVAL_API_TOKEN,
            )
        assert len(results) == 2
        assert "missing mirror" in (results[0].error or "")
        assert results[1].completed is True
        assert client.complete.call_count == 1

    async def test_uses_related_baseline_and_full_pinned_shas(
        self, test_db_session, tmp_path
    ) -> None:
        await _seed(test_db_session)
        sample = tmp_path / "s.json"
        sample.write_text(
            "[{\"source\": \"github\", \"project\": \"craft-parts\", \"external_id\": \"1\"}]"
        )
        client = AsyncMock()
        client.complete.return_value = _final()
        with (
            patch("scripts.llm.bakeoff.scoring_pilot.make_client", return_value=client),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._load_pinned_shas",
                return_value=(
                    {"craft-parts": "a" * 40, "rockcraft": "b" * 40},
                    {},
                ),
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._allowed_projects",
                new=AsyncMock(
                    return_value={
                        "craft-parts": "canonical",
                        "rockcraft": "canonical",
                    }
                ),
            ),
            patch(
                "scripts.llm.bakeoff.scoring_pilot._load_related_baseline",
                new=AsyncMock(return_value=[{"ref": "craft-parts#9"}]),
            ) as related,
            patch(
                "scripts.llm.bakeoff.scoring_pilot.build_round1_baseline",
                return_value="B",
            ) as baseline,
            patch(
                "scripts.llm.bakeoff.scoring_pilot._run_one",
                new=AsyncMock(
                    return_value=(
                        BakeoffResult(
                            issue_ref="craft-parts#1",
                            model="model-a",
                            backend="openrouter",
                            completed=True,
                            new_output={"scores": {"impact": 70}},
                        ),
                        [],
                    )
                ),
            ) as run_one,
        ):
            await run_scoring_pilot(
                session=test_db_session,
                models=["model-a"],
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=tmp_path / "t",
                mirror_dir=tmp_path / "m",
                api_key="k",
                eval_server_base_url="https://eval.example",
                eval_api_token=TEST_EVAL_API_TOKEN,
            )
        related.assert_awaited_once()
        assert baseline.call_args.kwargs["related"] == [{"ref": "craft-parts#9"}]
        assert run_one.await_args.kwargs["tool_ctx"].pinned_shas == {
            "craft-parts": "a" * 40,
            "rockcraft": "b" * 40,
        }
        assert (
            run_one.await_args.kwargs["tool_ctx"].eval_server_base_url
            == "https://eval.example"
        )
        assert (
            run_one.await_args.kwargs["tool_ctx"].eval_api_token == TEST_EVAL_API_TOKEN
        )

    def test_pinned_sha_rejects_unknown_project(self, tmp_path) -> None:
        with pytest.raises(UnknownProjectError):
            _pinned_sha_for_project("../evil", tmp_path, {"craft-parts": "canonical"})
