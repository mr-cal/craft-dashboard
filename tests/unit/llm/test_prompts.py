"""Tests for LLM prompt templates."""

from craft_dashboard.llm.prompts import (
    _format_comments,
    _truncate_body,
    build_closed_evaluate_prompt,
    build_open_evaluate_prompt,
)


class TestTruncateBody:
    def test_short_body_returned_unchanged(self) -> None:
        body = "short body"
        assert _truncate_body(body) == "short body"

    def test_none_returns_no_body_placeholder(self) -> None:
        assert _truncate_body(None) == "(no body)"

    def test_empty_string_returns_no_body_placeholder(self) -> None:
        assert _truncate_body("") == "(no body)"

    def test_body_exactly_at_head_limit_not_truncated(self) -> None:
        body = "x" * 12000
        assert _truncate_body(body) == body

    def test_body_exactly_at_head_plus_tail_limit_not_truncated(self) -> None:
        body = "x" * 18000
        assert _truncate_body(body) == body

    def test_long_body_keeps_head_and_tail(self) -> None:
        head = "A" * 12000
        middle = "M" * 5000
        tail = "Z" * 6000
        body = head + middle + tail
        result = _truncate_body(body)
        assert result.startswith("A" * 12000)
        assert result.endswith("Z" * 6000)
        assert "\n\n[... truncated ...]\n\n" in result

    def test_long_body_drops_middle(self) -> None:
        body = "A" * 12000 + "DROPPED" * 1000 + "Z" * 6000
        result = _truncate_body(body)
        assert "DROPPED" not in result

    def test_truncated_body_length_is_head_plus_tail_plus_separator(self) -> None:
        body = "x" * 20000
        result = _truncate_body(body)
        separator = "\n\n[... truncated ...]\n\n"
        assert len(result) == 12000 + len(separator) + 6000


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
            pr_details={
                "review_status": "approved",
                "review_count": 2,
                "ci_passing": ["lint"],
                "ci_failing": [],
                "ci_pending": [],
                "unresolved_review_comments": 0,
                "diff_additions": 10,
                "diff_deletions": 5,
                "diff_files_changed": 3,
            },
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

    def test_issue_system_requires_reason_to_cite_specific_evidence(self) -> None:
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
        content = msgs[0]["content"]
        assert "cite specific evidence" in content.lower()
        assert "comment" in content.lower()
        assert "label" in content.lower()

    def test_issue_system_requires_reason_to_explain_low_confidence(self) -> None:
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
        content = msgs[0]["content"]
        assert "confidence is low" in content.lower()

    def test_pr_system_requires_reason_to_cite_specific_evidence(self) -> None:
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
        content = msgs[0]["content"]
        assert "cite specific evidence" in content.lower()

    def test_pr_system_requires_reason_to_explain_low_confidence(self) -> None:
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
        content = msgs[0]["content"]
        assert "confidence is low" in content.lower()


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

    def test_includes_pr_details_for_merged_pr(self) -> None:
        """A merged PR's final review/CI state is included in the summary prompt."""
        msgs = build_closed_evaluate_prompt(
            title="Add feature",
            body="Description",
            issue_type="pull_request",
            state="merged",
            labels=[],
            age_days=5,
            last_activity_days=1,
            author="dev",
            is_maintainer=False,
            comment_count=0,
            pr_details={
                "review_status": "approved",
                "review_count": 2,
                "ci_passing": ["lint"],
                "ci_failing": [],
                "ci_pending": [],
                "unresolved_review_comments": 0,
                "diff_additions": 10,
                "diff_deletions": 5,
                "diff_files_changed": 3,
            },
        )
        assert "approved" in msgs[1]["content"]

    def test_no_pr_details_for_closed_issue(self) -> None:
        """pr_details is ignored for non-PR issue_type, even if passed."""
        msgs = build_closed_evaluate_prompt(
            title="Bug",
            body=None,
            issue_type="issue",
            state="closed",
            labels=[],
            age_days=0,
            last_activity_days=0,
            author="x",
            is_maintainer=False,
            comment_count=0,
            pr_details={"review_status": "should_not_appear"},
        )
        assert "should_not_appear" not in msgs[1]["content"]

    def test_pr_details_omitted_when_none(self) -> None:
        """No error and no stray formatting when pr_details is not supplied."""
        msgs = build_closed_evaluate_prompt(
            title="Add feature",
            body="Description",
            issue_type="pull_request",
            state="merged",
            labels=[],
            age_days=5,
            last_activity_days=1,
            author="dev",
            is_maintainer=False,
            comment_count=0,
        )
        assert "Review status" not in msgs[1]["content"]
