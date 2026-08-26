"""Unit tests for craft_dashboard.llm.tools schema definitions."""

from __future__ import annotations

from craft_dashboard.llm.tools import TOOL_SCHEMAS


class TestToolSchemas:
    """Every tool schema is well-formed OpenAI tool-calling JSON Schema."""

    def test_all_seven_tools_are_defined(self) -> None:
        names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
        assert names == {
            "git_log_search",
            "git_log_path",
            "read_file",
            "grep_repo",
            "repo_layout",
            "related_issues",
            "issue_detail",
        }

    def test_every_schema_has_required_openai_shape(self) -> None:
        for schema in TOOL_SCHEMAS:
            assert schema["type"] == "function"
            function = schema["function"]
            assert isinstance(function["name"], str)
            assert isinstance(function["description"], str)
            assert function["description"]
            assert function["parameters"]["type"] == "object"
            assert "properties" in function["parameters"]

    def test_read_file_requires_project_and_path(self) -> None:
        read_file_schema = next(
            s for s in TOOL_SCHEMAS if s["function"]["name"] == "read_file"
        )
        required = read_file_schema["function"]["parameters"]["required"]
        assert "project" in required
        assert "path" in required

    def test_ref_is_optional_on_every_repo_scoped_tool(self) -> None:
        """ref defaults to the pinned HEAD SHA — never required."""
        repo_scoped = {
            "git_log_search",
            "git_log_path",
            "read_file",
            "grep_repo",
            "repo_layout",
        }
        for schema in TOOL_SCHEMAS:
            if schema["function"]["name"] in repo_scoped:
                required = schema["function"]["parameters"]["required"]
                assert "ref" not in required
