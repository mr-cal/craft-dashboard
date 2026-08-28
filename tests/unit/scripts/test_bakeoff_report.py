"""Unit tests for scripts.llm.bakeoff.report."""

from __future__ import annotations

from scripts.llm.bakeoff.common import BakeoffResult
from scripts.llm.bakeoff.report import (
    extrapolate_backfill,
    write_scoring_report,
    write_summary_report,
)


def _r(**kw):
    base = {
        "issue_ref": "craft-parts#1",
        "model": "z-ai/glm-5.2",
        "backend": "openrouter",
        "old_scores": {"complexity": 40},
        "new_output": {"scores": {"impact": 70}},
        "related_work": [],
        "rounds_used": 3,
        "tools_called": ["repo_layout"],
        "prompt_tokens": 40000,
        "completion_tokens": 2000,
        "cost_usd": 0.05,
        "wall_seconds": 12.0,
        "completed": True,
    }
    base.update(kw)
    return BakeoffResult(**base)


class TestScoringReport:
    def test_report_has_old_vs_new_and_effort_columns(self, tmp_path) -> None:
        out = tmp_path / "report_scoring.md"
        write_scoring_report(
            [_r()],
            out,
            max_rounds=6,
            sweep_results=[
                {
                    "cap": 3,
                    "completion_rate": 1.0,
                    "mean_cost_usd": 0.02,
                    "mean_wall_seconds": 3.0,
                    "score_change_fraction": 0.20,
                },
                {
                    "cap": 4,
                    "completion_rate": 1.0,
                    "mean_cost_usd": 0.03,
                    "mean_wall_seconds": 4.0,
                    "score_change_fraction": 0.05,
                },
            ],
        )
        text = out.read_text()
        assert "old" in text.lower()
        assert "new" in text.lower()
        assert "cost_usd" in text or "Cost" in text
        assert "rounds" in text.lower()
        assert "wall" in text.lower()
        assert "recommended max_tool_rounds" in text.lower()

    def test_report_includes_rationale_column_and_text(self, tmp_path) -> None:
        out = tmp_path / "report_scoring.md"
        write_scoring_report(
            [
                _r(
                    new_output={
                        "scores": {"impact": 70},
                        "rationale": "Cites craft_parts/executor/step_handler.py directly.",
                    }
                )
            ],
            out,
            max_rounds=6,
        )
        text = out.read_text()
        assert "rationale" in text.lower()
        assert "Cites craft_parts/executor/step_handler.py directly." in text


class TestExtrapolation:
    def test_projects_backfill_with_mean_and_stdev(self) -> None:
        results = [_r(cost_usd=0.04), _r(cost_usd=0.06)]
        proj = extrapolate_backfill(results, item_count=2269)
        assert proj["mean_cost_per_item"] == 0.05
        assert proj["projected_total"] == 0.05 * 2269
        assert proj["stdev_cost_per_item"] > 0


class TestSummaryReport:
    def test_writes_side_by_side_rows_and_recommendation(self, tmp_path) -> None:
        out = tmp_path / "report_summary.md"
        results = [
            _r(
                model="qwen/qwen3.6-35b-a3b",
                new_output={"summary": "incumbent"},
            ),
            _r(model="z-ai/glm-5.2", new_output={"summary": "glm"}),
            _r(model="qwen/qwen3.8-27b", new_output={"summary": "qwen"}),
            _r(
                model="deepseek/deepseek-v4-pro-0813",
                new_output={"summary": "deepseek"},
            ),
        ]
        grades = [
            {
                "issue_ref": "craft-parts#1",
                "model": "qwen/qwen3.6-35b-a3b",
                "faithfulness": 70,
                "grade": "pass",
            },
            {
                "issue_ref": "craft-parts#1",
                "model": "z-ai/glm-5.2",
                "faithfulness": 90,
                "grade": "pass",
            },
            {
                "issue_ref": "craft-parts#1",
                "model": "qwen/qwen3.8-27b",
                "faithfulness": 71,
                "grade": "pass",
            },
            {
                "issue_ref": "craft-parts#1",
                "model": "deepseek/deepseek-v4-pro-0813",
                "faithfulness": 72,
                "grade": "pass",
            },
        ]

        write_summary_report(results, grades, out)

        text = out.read_text()
        assert "craft-parts#1" in text
        assert "incumbent" in text
        assert "glm" in text
        assert "qwen" in text
        assert "deepseek" in text
        assert "Recommendation: switch to z-ai/glm-5.2" in text

    def test_writes_report_without_crashing_when_no_grades_supplied(
        self, tmp_path
    ) -> None:
        """summary_bakeoff.py always calls this with grades=[] since grading
        is a separate script (grade_transcripts.py); it must not crash."""
        out = tmp_path / "report_summary.md"
        results = [
            _r(model="qwen/qwen3.6-35b-a3b", new_output={"summary": "incumbent"}),
            _r(model="z-ai/glm-5.2", new_output={"summary": "glm"}),
        ]

        write_summary_report(results, [], out)

        text = out.read_text()
        assert "craft-parts#1" in text
        assert "incumbent" in text
        assert "glm" in text
        assert "no grades supplied" in text.lower()
