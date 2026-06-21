"""Tests for LLM prompt templates."""

from craft_dashboard.llm.prompts import (
    _format_comments,
    build_closed_evaluate_prompt,
    build_closed_summary_prompt,
    build_evaluation_prompt,
    build_open_evaluate_prompt,
    build_summary_prompt,
)


class TestBuildSummaryPrompt:
    """Tests for build_summary_prompt."""

    def test_returns_messages_list(self) -> None:
        """Returns a list of message dicts."""
        messages = build_summary_prompt(
            title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
            body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            issue_type="issue",
            labels=["bug", "priority-high"],
        )

        assert isinstance(messages, list)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_includes_issue_content(self) -> None:
        """The user message includes the issue title and body."""
        messages = build_summary_prompt(
            title="Add support for core24 base",
            body="Please add support for `base: core24` so new snaps can target Ubuntu 24.04.",
            issue_type="issue",
            labels=["enhancement", "snapcraft"],
        )

        user_msg = messages[1]["content"]
        assert "Add support for core24 base" in user_msg
        assert "base: core24" in user_msg

    def test_includes_state_context(self) -> None:
        """The user message includes age, activity, and author state."""
        messages = build_summary_prompt(
            title="charmcraft deploy times out on large bundles",
            body="Deploying a large bundle stalls while charmcraft waits for the controller response.",
            issue_type="issue",
            labels=["needs-triage"],
            age_days=120,
            last_activity_days=45,
            comment_count=14,
            author="sergio-cazzolato",
            is_maintainer=True,
        )

        user_msg = messages[1]["content"]
        assert "120" in user_msg
        assert "45" in user_msg
        assert "14" in user_msg
        assert "sergio-cazzolato" in user_msg
        assert "maintainer" in user_msg

    def test_system_prompt_mentions_256_characters(self) -> None:
        """The system prompt instructs the model to stay under 256 characters."""
        messages = build_summary_prompt(
            title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
            body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            issue_type="issue",
            labels=["bug", "priority-high"],
        )

        assert "256" in messages[0]["content"]


class TestBuildSummaryPromptWithComments:
    """Tests for build_summary_prompt with comments."""

    def test_includes_comments_in_prompt(self) -> None:
        """Comments section appears in the user message."""
        comments = [
            {
                "author": "jdoe-canonical",
                "body": "I can still reproduce this on Ubuntu 24.04 with `snapcraft pack --use-lxd`.",
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            },
        ]
        messages = build_summary_prompt(
            title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
            body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            issue_type="issue",
            labels=["bug", "priority-high"],
            comments=comments,
        )

        user_msg = messages[1]["content"]
        assert "jdoe-canonical" in user_msg
        assert "snapcraft pack --use-lxd" in user_msg

    def test_no_comments_omits_section(self) -> None:
        """When no comments provided, the comments section is absent."""
        messages = build_summary_prompt(
            title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
            body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            issue_type="issue",
            labels=["bug", "priority-high"],
            comments=[],
        )

        assert "Comments" not in messages[1]["content"]

    def test_comments_default_empty(self) -> None:
        """comments parameter defaults to empty list (backward compat)."""
        messages = build_summary_prompt(
            title="Add support for core24 base",
            body="Please add support for `base: core24` so new snaps can target Ubuntu 24.04.",
            issue_type="issue",
            labels=["enhancement", "snapcraft"],
        )

        assert isinstance(messages, list)


class TestBuildClosedSummaryPrompt:
    """Tests for build_closed_summary_prompt."""

    def test_includes_closed_state_and_resolution_context(self) -> None:
        """Closed summaries mention the final state instead of live triage context."""
        messages = build_closed_summary_prompt(
            title="fix: handle empty manifest gracefully",
            body="Guard against empty manifest data when rendering snap metadata during pack.",
            issue_type="pull_request",
            state="merged",
            labels=["bug", "snapcraft"],
            age_days=12,
            last_activity_days=0,
            comment_count=3,
            author="craft-contributor",
            is_maintainer=False,
        )

        assert "what happened" in messages[0]["content"].lower()
        assert "State: merged" in messages[1]["content"]
        assert "current state" not in messages[0]["content"].lower()


class TestBuildEvaluationPrompt:
    """Tests for build_evaluation_prompt."""

    def test_returns_messages_list(self) -> None:
        """Returns a list of message dicts."""
        messages = build_evaluation_prompt(
            title="fix: handle empty manifest gracefully",
            body="Guard against empty manifest data when rendering snap metadata during pack.",
            issue_type="pull_request",
            labels=["bug", "snapcraft"],
            age_days=120,
            last_activity_days=45,
            author="craft-contributor",
            is_maintainer=False,
            comment_count=6,
        )

        assert isinstance(messages, list)
        assert len(messages) == 2

    def test_pr_specific_scores(self) -> None:
        """PR evaluation prompt mentions confidence score."""
        messages = build_evaluation_prompt(
            title="Add support for core24 base",
            body="Implements `base: core24` support for snapcraft project definitions.",
            issue_type="pull_request",
            labels=["enhancement", "snapcraft"],
            age_days=45,
            last_activity_days=5,
            author="sergio-cazzolato",
            is_maintainer=True,
            comment_count=10,
        )

        system_msg = messages[0]["content"]
        assert "confidence" in system_msg.lower()

    def test_issue_specific_scores(self) -> None:
        """Issue evaluation prompt mentions support_request score."""
        messages = build_evaluation_prompt(
            title="How do I debug charmcraft remote-build failures?",
            body="I cannot tell whether `charmcraft remote-build` is failing in Launchpad or during upload.",
            issue_type="issue",
            labels=["needs-triage"],
            age_days=45,
            last_activity_days=12,
            author="craft-contributor",
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
            {
                "author": "sergio-cazzolato",
                "body": "Please add a regression test for the empty manifest case.",
                "created_at": "2024-06-01T00:00:00+00:00",
                "type": "review_comment",
            },
        ]
        messages = build_evaluation_prompt(
            title="fix: handle empty manifest gracefully",
            body="Guard against empty manifest data when rendering snap metadata during pack.",
            issue_type="pull_request",
            labels=["bug", "snapcraft"],
            age_days=45,
            last_activity_days=2,
            author="craft-contributor",
            is_maintainer=False,
            comment_count=1,
            comments=comments,
        )

        user_msg = messages[1]["content"]
        assert "sergio-cazzolato" in user_msg
        assert "regression test for the empty manifest case" in user_msg

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
            title="Add support for core24 base",
            body="Implements `base: core24` support for snapcraft project definitions.",
            issue_type="pull_request",
            labels=["enhancement", "snapcraft"],
            age_days=45,
            last_activity_days=4,
            author="sergio-cazzolato",
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
            title="snapcraft pack fails with LXD backend on Ubuntu 24.04",
            body="When running `snapcraft pack` with the LXD backend, the build fails during prime with a mount namespace error.",
            issue_type="issue",
            labels=["bug", "priority-high"],
            age_days=45,
            last_activity_days=3,
            author="jdoe-canonical",
            is_maintainer=False,
            comment_count=0,
        )

        user_msg = messages[1]["content"]
        assert "CI checks" not in user_msg
        assert "review_status" not in user_msg

    def test_backward_compat_no_new_params(self) -> None:
        """Existing callers without comments/pr_details still work."""
        messages = build_evaluation_prompt(
            title="charmcraft deploy times out on large bundles",
            body="Deploying a large bundle stalls while charmcraft waits for the controller response.",
            issue_type="issue",
            labels=["needs-triage"],
            age_days=45,
            last_activity_days=12,
            author="craft-contributor",
            is_maintainer=False,
            comment_count=0,
        )

        assert isinstance(messages, list)
        assert len(messages) == 2


class TestFormatCommentsEdgeCases:
    """Edge case tests for _format_comments helper."""

    def test_missing_author_key(self) -> None:
        """Missing 'author' key falls back to 'unknown'."""
        comments = [
            {
                "body": "Please confirm whether this still reproduces on core24.",
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            }
        ]
        result = _format_comments(comments)

        assert "unknown" in result
        assert "Please confirm whether this still reproduces on core24." in result

    def test_missing_body_key(self) -> None:
        """Missing 'body' key falls back to '(no comment)'."""
        comments = [
            {
                "author": "craft-contributor",
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            }
        ]
        result = _format_comments(comments)

        assert "craft-contributor" in result
        assert "(no comment)" in result

    def test_none_body(self) -> None:
        """None body falls back to '(no comment)'."""
        comments = [
            {
                "author": "sergio-cazzolato",
                "body": None,
                "created_at": "2024-01-01T00:00:00+00:00",
                "type": "comment",
            }
        ]
        result = _format_comments(comments)

        assert "(no comment)" in result

    def test_none_created_at(self) -> None:
        """None created_at produces empty date field without crash."""
        comments = [
            {
                "author": "jdoe-canonical",
                "body": "Needs a reproducer from core24 users.",
                "created_at": None,
                "type": "comment",
            }
        ]
        result = _format_comments(comments)

        assert "jdoe-canonical" in result
        assert "Needs a reproducer from core24 users." in result


class TestBuildOpenEvaluatePrompt:
    """Tests for build_open_evaluate_prompt."""

    def test_returns_messages_list_issue(self) -> None:
        msgs = build_open_evaluate_prompt(
            title="Crash on startup",
            body="Steps: 1. Install 2. Run",
            issue_type="issue",
            labels=["bug"],
            age_days=30,
            last_activity_days=5,
            author="alice",
            is_maintainer=False,
            comment_count=2,
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert '"summary"' in msgs[0]["content"]
        assert '"scores"' in msgs[0]["content"]
        assert '"suggested_action"' in msgs[0]["content"]
        assert "256" in msgs[0]["content"]
        assert msgs[1]["role"] == "user"
        assert "Crash on startup" in msgs[1]["content"]

    def test_issue_system_has_support_request_score(self) -> None:
        msgs = build_open_evaluate_prompt(
            title="T",
            body=None,
            issue_type="issue",
            labels=[],
            age_days=0,
            last_activity_days=0,
            author="x",
            is_maintainer=False,
            comment_count=0,
        )
        assert "support_request" in msgs[0]["content"]

    def test_pr_system_has_no_support_request_score(self) -> None:
        msgs = build_open_evaluate_prompt(
            title="Fix auth bug",
            body=None,
            issue_type="pull_request",
            labels=[],
            age_days=10,
            last_activity_days=1,
            author="bob",
            is_maintainer=True,
            comment_count=0,
        )
        assert "support_request" not in msgs[0]["content"]
        assert "needs_review" in msgs[0]["content"]

    def test_includes_pr_details(self) -> None:
        msgs = build_open_evaluate_prompt(
            title="Add feature",
            body="Description",
            issue_type="pull_request",
            labels=[],
            age_days=5,
            last_activity_days=1,
            author="dev",
            is_maintainer=False,
            comment_count=0,
            pr_details={"review_status": "approved", "review_count": 2, "ci_passing": ["lint"], "ci_failing": [], "ci_pending": [], "unresolved_review_comments": 0, "diff_additions": 10, "diff_deletions": 5, "diff_files_changed": 3},
        )
        assert "approved" in msgs[1]["content"]

    def test_no_pr_details_for_issue(self) -> None:
        msgs = build_open_evaluate_prompt(
            title="Bug",
            body=None,
            issue_type="issue",
            labels=[],
            age_days=0,
            last_activity_days=0,
            author="x",
            is_maintainer=False,
            comment_count=0,
            pr_details={"review_status": "should_not_appear"},
        )
        assert "should_not_appear" not in msgs[1]["content"]


class TestBuildClosedEvaluatePrompt:
    """Tests for build_closed_evaluate_prompt."""

    def test_returns_messages_list(self) -> None:
        msgs = build_closed_evaluate_prompt(
            title="Old bug",
            body="It crashed",
            issue_type="issue",
            state="closed",
            labels=[],
            age_days=365,
            last_activity_days=300,
            author="charlie",
            is_maintainer=False,
            comment_count=1,
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_system_asks_for_json_summary(self) -> None:
        msgs = build_closed_evaluate_prompt(
            title="T",
            body=None,
            issue_type="issue",
            state="closed",
            labels=[],
            age_days=10,
            last_activity_days=5,
            author="x",
            is_maintainer=False,
            comment_count=0,
        )
        assert '"summary"' in msgs[0]["content"]

    def test_system_does_not_ask_for_scores(self) -> None:
        msgs = build_closed_evaluate_prompt(
            title="T",
            body=None,
            issue_type="issue",
            state="closed",
            labels=[],
            age_days=10,
            last_activity_days=5,
            author="x",
            is_maintainer=False,
            comment_count=0,
        )
        assert "scores" not in msgs[0]["content"]

    def test_user_content_includes_state(self) -> None:
        msgs = build_closed_evaluate_prompt(
            title="Old bug",
            body="It crashed",
            issue_type="issue",
            state="closed",
            labels=[],
            age_days=365,
            last_activity_days=300,
            author="charlie",
            is_maintainer=False,
            comment_count=1,
        )
        assert "closed" in msgs[1]["content"].lower()
        assert "Old bug" in msgs[1]["content"]

