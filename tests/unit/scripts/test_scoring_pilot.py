"""Unit tests for scripts.llm.bakeoff.scoring_pilot."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from craft_dashboard.git_mirrors.exceptions import UnknownProjectError
from craft_dashboard.llm.client import LLMResponse
from scripts.llm.bakeoff.common import BakeoffResult
from scripts.llm.bakeoff.scoring_pilot import (
    _pinned_sha_for_project,
    _raise_pin_error,
    cli,
    run_max_rounds_sweep,
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

    async def test_transcript_captures_model_reasoning(
        self, test_db_session, tmp_path
    ) -> None:
        """The model's reasoning trace (when the provider returns one) must
        be written into the per-round transcript entry, not discarded --
        otherwise debug transcripts have no visibility into *why* a model
        reached a given score.
        """
        await _seed(test_db_session)
        sample = tmp_path / "s.json"
        sample.write_text(
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
        )
        client = AsyncMock()
        client.complete.return_value = LLMResponse(
            content='{"scores": {"impact": 70}, "related_work": []}',
            prompt_tokens=200,
            completion_tokens=40,
            total_tokens=240,
            model="model-a",
            cost_usd=0.002,
            tool_calls=None,
            reasoning="Considered the issue title and decided impact=70.",
            finish_reason="stop",
            reasoning_tokens=12,
        )
        transcripts_dir = tmp_path / "t"
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
            await run_scoring_pilot(
                session=test_db_session,
                models=["model-a"],
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=transcripts_dir,
                mirror_dir=tmp_path / "m",
                api_key="k",
                max_rounds=6,
            )

        transcript_files = list(transcripts_dir.glob("*.json"))
        assert len(transcript_files) == 1
        data = json.loads(transcript_files[0].read_text())
        assert data["transcript"][0]["reasoning"] == (
            "Considered the issue title and decided impact=70."
        )
        assert data["transcript"][0]["finish_reason"] == "stop"
        assert data["transcript"][0]["reasoning_tokens"] == 12

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

    async def test_round_one_requires_a_tool_call_later_rounds_are_auto(
        self, test_db_session, tmp_path
    ) -> None:
        """Regression test: the real bake-off found challenger models
        overwhelmingly skipped tool calls under tool_choice="auto" alone,
        producing all-zero/fabricated-looking scores with no gathered
        evidence (judge-graded ~0% pass rate). Round 1 must force a tool
        call; later rounds may still let the model choose to finalize."""
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
            await run_scoring_pilot(
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
        first_call, second_call = client.complete.call_args_list
        assert first_call.kwargs["tool_choice"] == "required"
        assert second_call.kwargs["tool_choice"] == "auto"

    async def test_rejects_degenerate_response_with_excessive_tool_calls(
        self, test_db_session, tmp_path
    ) -> None:
        """Regression test: the real bake-off hit a response with 209
        near-identical tool calls in a single turn (a degenerate/looping
        model output), which blew the token budget and then crashed the
        follow-up request. Such responses must fail fast instead of being
        dispatched."""
        await _seed(test_db_session)
        sample = tmp_path / "s.json"
        sample.write_text(
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
        )
        runaway = LLMResponse(
            content="",
            prompt_tokens=200,
            completion_tokens=20,
            total_tokens=220,
            model="model-a",
            cost_usd=0.001,
            tool_calls=[
                {
                    "id": f"c{i}",
                    "type": "function",
                    "function": {
                        "name": "grep_repo",
                        "arguments": '{"project": "craft-parts", "pattern": "x"}',
                    },
                }
                for i in range(50)
            ],
        )
        client = AsyncMock()
        client.complete.return_value = runaway
        dispatch = AsyncMock(return_value="x")
        with (
            patch("scripts.llm.bakeoff.scoring_pilot.make_client", return_value=client),
            patch(
                "scripts.llm.bakeoff.scoring_pilot.build_round1_baseline",
                return_value="B",
            ),
            patch("scripts.llm.bakeoff.scoring_pilot.dispatch_tool_call", new=dispatch),
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
        assert results[0].completed is False
        assert "degenerate response" in (results[0].error or "")
        assert "50" in (results[0].error or "")
        dispatch.assert_not_awaited()

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
            '[{"source":"github","project":"craft-parts","external_id":"1"},'
            '{"source":"github","project":"rockcraft","external_id":"2"}]'
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
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
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

    def test_cli_rejects_sweep_with_multiple_models_before_running(
        self, monkeypatch
    ) -> None:
        runner = CliRunner()
        run_pilot = AsyncMock()
        monkeypatch.setattr(
            "scripts.llm.bakeoff.scoring_pilot.run_scoring_pilot", run_pilot
        )
        monkeypatch.setattr(
            "scripts.llm.bakeoff.scoring_pilot.run_max_rounds_sweep", AsyncMock()
        )

        result = runner.invoke(
            cli,
            [
                "--model",
                "m1",
                "--model",
                "m2",
                "--sweep",
                "--sample",
                "scripts/llm/bakeoff/scoring_sample.json",
                "--transcripts-dir",
                ".bakeoff/tests",
                "--mirror-dir",
                ".",
                "--out",
                ".bakeoff/tests/report.md",
                "--eval-server-base-url",
                "https://eval.example",
                "--eval-api-token",
                TEST_EVAL_API_TOKEN,
            ],
        )

        assert result.exit_code != 0
        assert "--sweep requires exactly one --model" in result.output
        run_pilot.assert_not_called()

    def test_pinned_sha_rejects_unknown_project(self, tmp_path) -> None:
        with pytest.raises(UnknownProjectError):
            _pinned_sha_for_project("../evil", tmp_path, {"craft-parts": "canonical"})


class TestRaisePinError:
    """Regression tests for the "snapcraft (launchpad)" pin-error lookup.

    ``pin_errors`` is keyed by real git-mirror project names (built from
    ``allowed_projects``, which never contains Launchpad-view rows like
    "snapcraft (launchpad)"), so a raw non-canonical lookup would silently
    miss a real pin failure for the underlying "snapcraft" mirror.
    """

    def test_finds_pin_error_via_canonical_launchpad_name(self) -> None:
        with pytest.raises(RuntimeError, match="mirror unavailable"):
            _raise_pin_error(
                "snapcraft (launchpad)", {"snapcraft": "mirror unavailable"}
            )

    def test_no_error_when_canonical_name_has_no_pin_error(self) -> None:
        _raise_pin_error("snapcraft (launchpad)", {})  # must not raise

    def test_finds_pin_error_for_plain_project_name(self) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            _raise_pin_error("craft-parts", {"craft-parts": "boom"})


class TestRunMaxRoundsSweep:
    async def test_reports_score_change_fraction_between_caps(
        self, test_db_session, tmp_path
    ) -> None:
        sample = tmp_path / "s.json"
        sample.write_text("[]")
        base = BakeoffResult(
            issue_ref="craft-parts#1",
            model="model-a",
            backend="openrouter",
            new_output={"scores": {"impact": 70}},
            cost_usd=0.02,
            wall_seconds=2.0,
            completed=True,
        )
        changed = BakeoffResult(
            issue_ref="craft-parts#1",
            model="model-a",
            backend="openrouter",
            new_output={"scores": {"impact": 80}},
            cost_usd=0.03,
            wall_seconds=3.0,
            completed=True,
        )
        with patch(
            "scripts.llm.bakeoff.scoring_pilot.run_scoring_pilot",
            new=AsyncMock(side_effect=[[base], [base], [changed]]),
        ):
            sweep = await run_max_rounds_sweep(
                session=test_db_session,
                model="model-a",
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=Path(),
                mirror_dir=Path(),
                caps=(3, 4, 6),
            )
        assert [row["cap"] for row in sweep] == [3, 4, 6]
        assert sweep[1]["score_change_fraction"] == 0.0
        assert sweep[2]["score_change_fraction"] == 1.0

    async def test_applies_cumulative_spend_budget_across_caps(
        self, test_db_session, tmp_path
    ) -> None:
        sample = tmp_path / "s.json"
        sample.write_text("[]")
        first = BakeoffResult(
            issue_ref="craft-parts#1",
            model="model-a",
            backend="openrouter",
            cost_usd=0.02,
            completed=True,
        )
        second = BakeoffResult(
            issue_ref="craft-parts#1",
            model="model-a",
            backend="openrouter",
            cost_usd=0.03,
            completed=True,
        )
        run_pilot = AsyncMock(side_effect=[[first], [second]])
        with patch(
            "scripts.llm.bakeoff.scoring_pilot.run_scoring_pilot",
            new=run_pilot,
        ):
            await run_max_rounds_sweep(
                session=test_db_session,
                model="model-a",
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=Path(),
                mirror_dir=Path(),
                caps=(3, 4),
                max_spend_usd=0.10,
            )
        assert run_pilot.await_args_list[0].kwargs["max_spend_usd"] == 0.10
        assert run_pilot.await_args_list[1].kwargs["max_spend_usd"] == 0.08

    async def test_seeds_cumulative_spend_and_skips_a_cap(
        self, test_db_session, tmp_path
    ) -> None:
        """A prior (e.g. base) run's spend and cap are carried into the sweep.

        Regression test: previously the CLI ran the base --max-rounds pilot
        and the sweep against the *same* max_spend_usd independently (double
        budget), and the sweep always re-ran cap == max_rounds, duplicating
        the base run's cost. See scoring_pilot.py cli() for the fix.
        """
        sample = tmp_path / "s.json"
        sample.write_text("[]")
        only_call = BakeoffResult(
            issue_ref="craft-parts#1",
            model="model-a",
            backend="openrouter",
            cost_usd=0.03,
            completed=True,
        )
        run_pilot = AsyncMock(return_value=[only_call])
        with patch(
            "scripts.llm.bakeoff.scoring_pilot.run_scoring_pilot",
            new=run_pilot,
        ):
            await run_max_rounds_sweep(
                session=test_db_session,
                model="model-a",
                backend="openrouter",
                sample_path=sample,
                transcripts_dir=Path(),
                mirror_dir=Path(),
                caps=(3, 4, 6),
                max_spend_usd=0.10,
                initial_spend_usd=0.05,
                skip_caps=frozenset({6}),
            )
        # cap 6 skipped entirely: only caps 3 and 4 ran.
        assert run_pilot.await_count == 2
        # budget carried forward from the base run's spend (0.05), not reset.
        assert run_pilot.await_args_list[0].kwargs["max_spend_usd"] == 0.05
        assert run_pilot.await_args_list[1].kwargs["max_spend_usd"] == pytest.approx(
            0.02
        )
