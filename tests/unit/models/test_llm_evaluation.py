"""Tests for the LLMEvaluation model."""

from craft_dashboard.models.llm_evaluation import LLMEvaluation


class TestLLMEvaluationModel:
    """Tests for the LLMEvaluation model."""

    def test_tablename(self) -> None:
        """LLMEvaluation model uses 'llm_evaluations' table."""
        assert LLMEvaluation.__tablename__ == "llm_evaluations"

    def test_required_columns(self) -> None:
        """LLMEvaluation model has all required columns."""
        column_names = {col.name for col in LLMEvaluation.__table__.columns}
        expected = {
            "id",
            "issue_id",
            "model_name",
            "summary",
            "suggested_action",
            "suggested_action_reason",
            "scores",
            "tokens_used",
            "evaluated_at",
            "issue_data_hash",
            "latest",
        }
        assert expected.issubset(column_names)

    def test_partial_unique_index_on_latest(self) -> None:
        """A partial unique index enforces only one latest=true row per issue."""
        indexes = LLMEvaluation.__table__.indexes
        partial_unique = [
            idx
            for idx in indexes
            if idx.unique and {col.name for col in idx.columns} == {"issue_id"}
        ]
        assert len(partial_unique) == 1

    def test_issue_id_foreign_key(self) -> None:
        """issue_id references issues.id."""
        col = LLMEvaluation.__table__.columns["issue_id"]
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert "issues.id" in fk_targets
