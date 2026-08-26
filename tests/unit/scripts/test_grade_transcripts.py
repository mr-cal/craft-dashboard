"""Unit tests for scripts.grade_transcripts."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from craft_dashboard.llm.client import LLMResponse
from scripts.grade_transcripts import (
    grade_transcripts,
    load_transcripts,
    summarize_grades,
)


class TestLoadTranscripts:
    def test_loads_every_json_file(self, tmp_path) -> None:
        (tmp_path / "a.json").write_text(
            json.dumps({"issue_ref": "a#1", "rounds_used": 2})
        )
        (tmp_path / "b.json").write_text(
            json.dumps({"issue_ref": "b#2", "rounds_used": 3})
        )
        assert {t["issue_ref"] for t in load_transcripts(tmp_path)} == {"a#1", "b#2"}


class TestGradeWithJudgeModel:
    async def test_calls_judge_model_per_transcript(self, tmp_path) -> None:
        (tmp_path / "a.json").write_text(
            json.dumps(
                {
                    "issue_ref": "a#1",
                    "rounds_used": 2,
                    "tools_called": ["repo_layout"],
                    "final_output": {"scores": {"impact": 70}},
                    "transcript": [],
                }
            )
        )
        client = AsyncMock()
        client.complete.return_value = LLMResponse(
            content='{"grade": "pass", "wasted_calls": 0, "missed_evidence": [], "premature": false, "note": "solid"}',
            prompt_tokens=100,
            completion_tokens=30,
            total_tokens=130,
            model="judge",
            cost_usd=0.001,
        )
        with patch("scripts.grade_transcripts.make_client", return_value=client):
            grades = await grade_transcripts(
                tmp_path,
                judge_model="judge",
                backend="openrouter",
                api_key="k",
            )
        assert client.complete.call_count == 1
        assert grades[0]["grade"] == "pass"

    def test_summarize_pass_rate(self) -> None:
        grades = [
            {"grade": "pass", "rounds_used": 2},
            {"grade": "fail", "rounds_used": 4},
        ]
        s = summarize_grades(grades)
        assert s["pass_rate"] == 0.5
        assert s["average_rounds"] == 3.0
