"""Unit tests for scripts.llm.bakeoff.summary_bakeoff."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from craft_dashboard.llm.client import LLMResponse
from scripts.llm.bakeoff.summary_bakeoff import CANDIDATES, run_summary_bakeoff

from tests.factories import make_issue, make_project


class TestSummaryBakeoff:
    async def test_includes_incumbent_as_full_candidate(self) -> None:
        assert "qwen/qwen3.6-35b-a3b" in CANDIDATES
        assert "z-ai/glm-5.2" in CANDIDATES
        assert "qwen/qwen3.8-27b" in CANDIDATES
        assert "deepseek/deepseek-v4-pro-0813" in CANDIDATES

    async def test_uses_closed_prompt_and_records_summary(
        self, test_db_session, tmp_path
    ) -> None:
        test_db_session.add_all(
            [
                make_project(id=1, name="craft-parts"),
                make_issue(
                    id=1,
                    project_id=1,
                    source="github",
                    external_id="1",
                    title="Bug",
                    state="closed",
                ),
            ]
        )
        await test_db_session.commit()
        sample = tmp_path / "s.json"
        sample.write_text(
            '[{"source": "github", "project": "craft-parts", "external_id": "1"}]'
        )

        client = AsyncMock()
        client.complete.return_value = LLMResponse(
            content='{"summary": "Merged via #42; fixes the pull-step handler."}',
            prompt_tokens=80,
            completion_tokens=15,
            total_tokens=95,
            model="model-a",
            cost_usd=0.0008,
        )

        with (
            patch(
                "scripts.llm.bakeoff.summary_bakeoff.make_client",
                return_value=client,
            ),
            patch(
                "scripts.llm.bakeoff.summary_bakeoff.build_summary_messages",
            ) as spy,
        ):
            spy.return_value = [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "u"},
            ]
            results = await run_summary_bakeoff(
                session=test_db_session,
                models=["model-a"],
                backend="openrouter",
                sample_path=sample,
                api_key="k",
            )

        assert results[0].new_output["summary"].startswith("Merged via #42")
