"""Tests for duplicate detection prompt builders."""

from __future__ import annotations

from craft_dashboard.llm.prompts import (
    build_duplicate_check_prompt,
    build_duplicate_summary_rewrite_prompt,
)


def test_duplicate_check_includes_both_issues():
    messages = build_duplicate_check_prompt(
        issue_a_title="Build fails on core24",
        issue_a_summary="Core24 build pipeline broken",
        issue_a_project="snapcraft",
        issue_b_title="core24 snap build error",
        issue_b_summary="Snap build errors on core24 base",
        issue_b_project="snapcraft",
        issue_b_external_id="1234",
    )
    assert len(messages) == 2
    user = messages[1]["content"]
    assert "Issue A (snapcraft):" in user
    assert "Issue B (#1234):" in user
    assert "Core24 build pipeline broken" in user
    assert "Snap build errors on core24 base" in user


def test_duplicate_check_cross_project_shows_project():
    messages = build_duplicate_check_prompt(
        issue_a_title="Feature request in snapcraft",
        issue_a_summary="Snapcraft summary",
        issue_a_project="snapcraft",
        issue_b_title="Feature in craft-parts",
        issue_b_summary="craft-parts summary",
        issue_b_project="craft-parts",
        issue_b_external_id="99",
    )
    user = messages[1]["content"]
    assert "(from craft-parts)" in user


def test_duplicate_check_same_project_no_extra_label():
    messages = build_duplicate_check_prompt(
        issue_a_title="Issue A",
        issue_a_summary="Summary A",
        issue_a_project="rockcraft",
        issue_b_title="Issue B",
        issue_b_summary="Summary B",
        issue_b_project="rockcraft",
        issue_b_external_id="5",
    )
    user = messages[1]["content"]
    assert "(from rockcraft)" not in user


def test_summary_rewrite_includes_refs_and_original():
    messages = build_duplicate_summary_rewrite_prompt(
        original_summary="Core24 build pipeline broken",
        duplicate_refs=["snapcraft#42", "craft-parts#7"],
    )
    assert len(messages) == 2
    user = messages[1]["content"]
    assert "Core24 build pipeline broken" in user
    assert "snapcraft#42, craft-parts#7" in user
