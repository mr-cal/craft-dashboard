"""Unit tests for tool-calling support in LLMResponse/OpenRouterClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from craft_dashboard.llm.client import LLMResponse, OpenRouterClient


class TestLLMResponseToolCalls:
    """Parsing a tool-call response shape."""

    def test_parses_tool_calls_from_response(self) -> None:
        data = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "grep_repo",
                                    "arguments": '{"pattern": "foo", "repos": ["craft-parts"]}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "z-ai/glm-5.2",
        }
        response = LLMResponse.from_api_response(data)
        assert response.content == ""
        assert response.tool_calls is not None
        assert response.tool_calls[0]["function"]["name"] == "grep_repo"

    def test_no_tool_calls_defaults_to_none(self) -> None:
        data = {
            "choices": [{"message": {"content": "plain text response"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "z-ai/glm-5.2",
        }
        response = LLMResponse.from_api_response(data)
        assert response.tool_calls is None

    def test_parses_qwen_xml_tool_calls_from_content(self) -> None:
        xml_content = (
            "<tool_call>\n"
            "<function=git_diff>\n"
            "<parameter=project>\n"
            "charmcraft\n"
            "</parameter>\n"
            "<parameter=ref>\n"
            "main\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        data = {
            "choices": [{"message": {"content": xml_content, "tool_calls": None}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "qwen3.6-35b-a3b-mtp-q6",
        }
        response = LLMResponse.from_api_response(data)
        assert response.tool_calls is not None
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0]["function"]["name"] == "git_diff"
        assert response.tool_calls[0]["function"]["arguments"] == (
            '{"project": "charmcraft", "ref": "main"}'
        )
        assert response.content == ""

    def test_parses_json_tool_calls_from_content(self) -> None:
        json_content = (
            '<tool_call>\n{"name": "grep_repo", "arguments": {"pattern": "def foo", '
            '"repos": ["charmcraft"]}}\n</tool_call>'
        )
        data = {
            "choices": [{"message": {"content": json_content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "qwen3.6-35b-a3b-mtp-q6",
        }
        response = LLMResponse.from_api_response(data)
        assert response.tool_calls is not None
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0]["function"]["name"] == "grep_repo"
        assert response.tool_calls[0]["function"]["arguments"] == (
            '{"pattern": "def foo", "repos": ["charmcraft"]}'
        )
        assert response.content == ""

    def test_parses_multiple_tool_calls_from_content(self) -> None:
        content = (
            "<tool_call>\n"
            "<function=git_diff>\n"
            "<parameter=project>charmcraft</parameter>\n"
            "<parameter=ref>main</parameter>\n"
            "</function>\n"
            "</tool_call>\n"
            "<tool_call>\n"
            "<function=grep_repo>\n"
            "<parameter=pattern>test</parameter>\n"
            '<parameter=repos>["craft-parts"]</parameter>\n'
            "</function>\n"
            "</tool_call>"
        )
        data = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "qwen3.6-35b-a3b-mtp-q6",
        }
        response = LLMResponse.from_api_response(data)
        assert response.tool_calls is not None
        assert len(response.tool_calls) == 2
        assert response.tool_calls[0]["function"]["name"] == "git_diff"
        assert response.tool_calls[1]["function"]["name"] == "grep_repo"
        assert response.tool_calls[1]["function"]["arguments"] == (
            '{"pattern": "test", "repos": ["craft-parts"]}'
        )
        assert response.content == ""

    def test_preserves_content_surrounding_tool_calls(self) -> None:
        content = (
            "<think>Investigating changes</think>\n"
            "<tool_call>\n"
            "<function=git_diff>\n"
            "<parameter=project>charmcraft</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        data = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "qwen3.6-35b-a3b-mtp-q6",
        }
        response = LLMResponse.from_api_response(data)
        assert response.tool_calls is not None
        assert response.tool_calls[0]["function"]["name"] == "git_diff"
        assert response.content == "<think>Investigating changes</think>"


class TestOpenRouterClientToolCalling:
    """OpenRouterClient.complete() forwards tools/tool_choice when given."""

    async def test_complete_includes_tools_in_payload_when_given(self) -> None:
        client = OpenRouterClient(api_key="test-key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "model": "z-ai/glm-5.2",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            tools = [
                {
                    "type": "function",
                    "function": {"name": "grep_repo", "parameters": {}},
                }
            ]
            await client.complete(
                model="z-ai/glm-5.2",
                messages=[{"role": "user", "content": "hi"}],
                tools=tools,
                tool_choice="auto",
            )
            _args, kwargs = mock_post.call_args
            assert kwargs["json"]["tools"] == tools
            assert kwargs["json"]["tool_choice"] == "auto"

    async def test_complete_omits_tools_when_not_given(self) -> None:
        client = OpenRouterClient(api_key="test-key")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "model": "z-ai/glm-5.2",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await client.complete(
                model="z-ai/glm-5.2", messages=[{"role": "user", "content": "hi"}]
            )
            _args, kwargs = mock_post.call_args
            assert "tools" not in kwargs["json"]
