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


class TestBuildSummaryPromptWithComments:
    """Tests for build_summary_prompt with comments."""

    def test_includes_comments_in_prompt(self) -> None:
        """Comments section appears in the user message."""
        comments = [
            {"author": "alice", "body": "Is this still relevant?", "created_at": "2024-01-01T00:00:00+00:00", "type": "comment"},
        ]
        messages = build_summary_prompt(
            title="Bug: crash on startup",
            body="App crashes.",
            issue_type="issue",
            labels=["bug"],
            comments=comments,
        )

        user_msg = messages[1]["content"]
        assert "alice" in user_msg
        assert "Is this still relevant?" in user_msg

    def test_no_comments_omits_section(self) -> None:
        """When no comments provided, the comments section is absent."""
        messages = build_summary_prompt(
            title="Bug",
            body="Body",
            issue_type="issue",
            labels=[],
            comments=[],
        )

        assert "Comments" not in messages[1]["content"]

    def test_comments_default_empty(self) -> None:
        """comments parameter defaults to empty list (backward compat)."""
        messages = build_summary_prompt(
            title="Bug",
            body="Body",
            issue_type="issue",
            labels=[],
        )

        assert isinstance(messages, list)


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


class TestBuildEvaluationPromptWithContext:
    """Tests for build_evaluation_prompt with comments and pr_details."""

    def test_includes_comments(self) -> None:
        """Comments section appears in evaluation prompt."""
        comments = [
            {"author": "bob", "body": "LGTM", "created_at": "2024-06-01T00:00:00+00:00", "type": "review_comment"},
        ]
        messages = build_evaluation_prompt(
            title="Fix: error handling",
            body="Fixed it.",
            issue_type="pull_request",
            labels=[],
            age_days=5,
            last_activity_days=1,
            author="dev",
            is_maintainer=False,
            comment_count=1,
            comments=comments,
        )

        user_msg = messages[1]["content"]
        assert "bob" in user_msg
        assert "LGTM" in user_msg

    def test_includes_pr_details(self) -> None:
        """PR review, CI, and diff details appear in evaluation prompt."""
        pr_details = {
            "review_status": "changes_requested",
            "review_count": 2,
            "unresolved_review_comments": 3,
            "ci_passing": ["lint", "build"],
            "ci_failing": ["integration-tests"],
            "ci_pending": [],
            "diff_additions": 142,
            "diff_deletions": 38,
            "diff_files_changed": 5,
        }
        messages = build_evaluation_prompt(
            title="feat: new feature",
            body="Added stuff.",
            issue_type="pull_request",
            labels=[],
            age_days=10,
            last_activity_days=2,
            author="dev",
            is_maintainer=False,
            comment_count=2,
            pr_details=pr_details,
        )

        user_msg = messages[1]["content"]
        assert "changes_requested" in user_msg
        assert "integration-tests" in user_msg
        assert "unresolved" in user_msg.lower()
        assert "+142" in user_msg or "142" in user_msg

    def test_no_pr_details_for_issue(self) -> None:
        """PR details section is absent for plain issues."""
        messages = build_evaluation_prompt(
            title="Bug",
            body="Body",
            issue_type="issue",
            labels=[],
            age_days=5,
            last_activity_days=1,
            author="user",
            is_maintainer=False,
            comment_count=0,
        )

        user_msg = messages[1]["content"]
        assert "CI checks" not in user_msg
        assert "review_status" not in user_msg

    def test_backward_compat_no_new_params(self) -> None:
        """Existing callers without comments/pr_details still work."""
        messages = build_evaluation_prompt(
            title="Bug",
            body="Body",
            issue_type="issue",
            labels=[],
            age_days=5,
            last_activity_days=1,
            author="user",
            is_maintainer=False,
            comment_count=0,
        )

        assert isinstance(messages, list)
        assert len(messages) == 2
