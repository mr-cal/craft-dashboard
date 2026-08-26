"""Unit tests for scripts.llm.bakeoff.prompt_lab."""

from __future__ import annotations

import pytest
from craft_dashboard.git_mirrors.exceptions import UnknownProjectError
from scripts.llm.bakeoff.prompt_lab import build_round1_baseline, build_scoring_messages


class TestBuildScoringMessages:
    def test_system_prompt_asks_for_tools_and_new_schema(self) -> None:
        msgs = build_scoring_messages(
            title="Bug",
            body="boom",
            issue_type="issue",
            labels=[],
            project="craft-parts",
            baseline="LAYOUT…",
        )
        system = msgs[0]["content"]
        assert "impact" in system
        assert "quick_win" in system or "related_work" in system
        assert "tool" in system.lower()

    def test_baseline_is_embedded_as_untrusted_data(self) -> None:
        msgs = build_scoring_messages(
            title="Bug",
            body="boom",
            issue_type="issue",
            labels=[],
            project="craft-parts",
            baseline="LAYOUT-XYZ",
        )
        user = msgs[1]["content"]
        assert "LAYOUT-XYZ" in user


class TestBuildRound1Baseline:
    def test_returns_well_formed_sections_even_when_empty(self, tmp_path) -> None:
        baseline = build_round1_baseline(
            project="craft-parts",
            body=None,
            mirror_dir=tmp_path,
            allowed_projects={"craft-parts": "canonical"},
            related=[],
        )
        assert "repo layout" in baseline.lower()
        assert baseline

    def test_wraps_each_section_in_untrusted_delimiters(self, tmp_path) -> None:
        baseline = build_round1_baseline(
            project="craft-parts",
            body='Traceback (most recent call last):\nFile "src/mod.py", line 9',
            mirror_dir=tmp_path,
            allowed_projects={"craft-parts": "canonical"},
            related=[{"ref": "craft-parts#2", "title": "Related"}],
        )
        assert baseline.count("<<<BEGIN UNTRUSTED DATA>>>") >= 5

    def test_rejects_unknown_projects_before_path_join(self, tmp_path) -> None:
        with pytest.raises(UnknownProjectError):
            build_round1_baseline(
                project="../evil",
                body=None,
                mirror_dir=tmp_path,
                allowed_projects={"craft-parts": "canonical"},
                related=[],
            )
