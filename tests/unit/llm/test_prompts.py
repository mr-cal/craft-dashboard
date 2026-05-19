"""Tests for LLM prompt templates."""

from craft_dashboard.llm.prompts import (
    build_evaluation_prompt,
    build_summary_prompt,
)


class TestBuildSummaryPrompt:
    """Tests for build_summary_prompt."""

    def test_returns_messages_list(self) -> None:
        """Returns a list of message dicts."""
        messages = build_summary_prompt(
            title="Bug: crash on startup",
            body="The app crashes when I open it.",
            issue_type="issue",
            labels=["bug"],
        )

        assert isinstance(messages, list)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_includes_issue_content(self) -> None:
        """The user message includes the issue title and body."""
        messages = build_summary_prompt(
            title="Feature request: dark mode",
            body="Please add dark mode support.",
            issue_type="issue",
            labels=["enhancement"],
        )

        user_msg = messages[1]["content"]
        assert "Feature request: dark mode" in user_msg
        assert "dark mode support" in user_msg


class TestBuildEvaluationPrompt:
    """Tests for build_evaluation_prompt."""

    def test_returns_messages_list(self) -> None:
        """Returns a list of message dicts."""
        messages = build_evaluation_prompt(
            title="Old PR",
            body="This PR was opened a year ago.",
            issue_type="pull_request",
            labels=[],
            age_days=365,
            last_activity_days=180,
            author="some-user",
            is_maintainer=False,
            comment_count=2,
        )

        assert isinstance(messages, list)
        assert len(messages) == 2

    def test_pr_specific_scores(self) -> None:
        """PR evaluation prompt mentions readiness score."""
        messages = build_evaluation_prompt(
            title="Add feature X",
            body="Implements feature X.",
            issue_type="pull_request",
            labels=[],
            age_days=30,
            last_activity_days=5,
            author="dev",
            is_maintainer=True,
            comment_count=10,
        )

        system_msg = messages[0]["content"]
        assert "readiness" in system_msg.lower()

    def test_issue_specific_scores(self) -> None:
        """Issue evaluation prompt mentions support_request score."""
        messages = build_evaluation_prompt(
            title="How do I install?",
            body="I can't figure out how to install this.",
            issue_type="issue",
            labels=[],
            age_days=10,
            last_activity_days=10,
            author="new-user",
            is_maintainer=False,
            comment_count=0,
        )

        system_msg = messages[0]["content"]
        assert "support_request" in system_msg.lower()
