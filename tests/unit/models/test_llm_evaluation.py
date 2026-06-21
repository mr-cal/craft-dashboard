"""Tests for the LLMEvaluation model."""

import ast
from pathlib import Path

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
            "eval_version",
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

    def test_summary_embedding_field_exists_and_defaults_to_none(self) -> None:
        """summary_embedding field must exist and default to None."""
        evaluation = LLMEvaluation(
            issue_id=1,
            model_name="test",
            summary="hello",
            latest=True,
        )
        assert hasattr(evaluation, "summary_embedding")
        assert evaluation.summary_embedding is None

    def test_eval_version_field_exists_and_defaults_to_none(self) -> None:
        """eval_version field must exist and default to None."""
        evaluation = LLMEvaluation(
            issue_id=1,
            model_name="test",
            summary="hello",
            latest=True,
        )
        assert hasattr(evaluation, "eval_version")
        assert evaluation.eval_version is None

    def test_partial_index_uses_text_expression(self) -> None:
        """The partial unique index should use text() not a raw string."""
        module_path = (
            Path(__file__).resolve().parents[3]
            / "craft_dashboard/models/llm_evaluation.py"
        )
        tree = ast.parse(module_path.read_text())

        llm_evaluation = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "LLMEvaluation"
        )
        table_args_assign = next(
            node
            for node in llm_evaluation.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__table_args__"
                for target in node.targets
            )
        )
        index_call = table_args_assign.value.elts[0]
        postgresql_where = next(
            keyword.value
            for keyword in index_call.keywords
            if keyword.arg == "postgresql_where"
        )

        assert isinstance(postgresql_where, ast.Call)
        assert isinstance(postgresql_where.func, ast.Name)
        assert postgresql_where.func.id == "text"
