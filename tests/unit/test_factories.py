"""Tests for shared test object factories."""

from datetime import UTC

from tests.factories import make_evaluation, make_issue, make_project


class TestMakeProject:
    """Tests for make_project."""

    def test_make_project_uses_defaults(self) -> None:
        project = make_project()

        assert project.name == "snapcraft"
        assert project.category == "application"
        assert project.github_org == "canonical"
        assert project.display_order == 1


class TestMakeIssue:
    """Tests for make_issue."""

    def test_make_issue_uses_defaults(self) -> None:
        issue = make_issue()

        assert issue.project_id == 1
        assert issue.source == "github"
        assert issue.external_id == "1"
        assert issue.title == "Test issue"
        assert issue.metadata_ == {}
        assert issue.comments == []
        assert issue.labels == []
        assert issue.last_fetched_at.tzinfo is UTC
        assert issue.url == "https://github.com/canonical/test/issues/1"


class TestMakeEvaluation:
    """Tests for make_evaluation."""

    def test_make_evaluation_uses_defaults(self) -> None:
        evaluation = make_evaluation()

        assert evaluation.issue_id == 1
        assert evaluation.model_name == "test-model"
        assert evaluation.summary == "Test summary"
        assert evaluation.suggested_action == "needs_triage"
        assert evaluation.suggested_action_reason == "Test reason"
        assert evaluation.scores == {}
        assert evaluation.latest is True
        assert evaluation.llm_backend == "test-backend"
        assert evaluation.issue_data_hash == "hash-1"
