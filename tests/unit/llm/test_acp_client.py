"""Tests for CopilotACPClient."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from craft_dashboard.llm.acp_client import CopilotACPClient
from craft_dashboard.llm.client import LLMClient
from craft_dashboard.llm.exceptions import LLMUnavailableError


def test_copilot_acp_client_satisfies_protocol() -> None:
    """CopilotACPClient satisfies the LLMClient protocol."""
    client = CopilotACPClient()
    assert isinstance(client, LLMClient)
    assert callable(client.complete)
    assert callable(client.check_quota)


@pytest.mark.asyncio
async def test_copilot_acp_client_missing_binary() -> None:
    """CopilotACPClient raises LLMUnavailableError if gh is not found."""
    client = CopilotACPClient(copilot_cmd=["nonexistent-gh-cmd"])
    with pytest.raises(LLMUnavailableError, match="not found"):
        await client.complete(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
        )


@pytest.mark.asyncio
async def test_copilot_acp_client_complete_flow() -> None:
    """Test full initialization, session creation, configuration, and prompt execution."""
    client = CopilotACPClient(cwd="/tmp")

    # Mock subprocess streams
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()

    mock_stdout = MagicMock()

    # Generate server responses when requests are written to stdin
    server_responses: dict[str, dict[str, Any]] = {
        "initialize": {"protocolVersion": 1},
        "session/new": {"sessionId": "test-sess-123"},
        "session/set_config_option": {},
        "session/prompt": {
            "stopReason": "end_turn",
            "usage": {
                "inputTokens": 100,
                "outputTokens": 20,
                "totalTokens": 120,
                "thoughtTokens": 10,
            },
        },
    }

    line_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def mock_write(data: bytes) -> None:
        payload = json.loads(data.decode("utf-8"))
        req_id = payload.get("id")
        method = payload.get("method")

        if method == "session/prompt":
            # Emit a streaming update first
            update_msg = {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "test-sess-123",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": '{"summary": "great PR"}'},
                    },
                },
            }
            line_queue.put_nowait((json.dumps(update_msg) + "\n").encode("utf-8"))

        res = server_responses.get(method, {})
        resp_msg = {"jsonrpc": "2.0", "id": req_id, "result": res}
        line_queue.put_nowait((json.dumps(resp_msg) + "\n").encode("utf-8"))

    mock_stdin.write = mock_write

    async def mock_readline() -> bytes:
        return await line_queue.get()

    mock_stdout.readline = mock_readline

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.stdin = mock_stdin
    mock_proc.stdout = mock_stdout
    mock_proc.terminate = MagicMock()
    mock_proc.wait = AsyncMock()

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec,
    ):
        resp = await client.complete(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "summarize this"}],
            response_format={"type": "json_object"},
        )

        assert resp.content == '{"summary": "great PR"}'
        assert resp.model == "gemini-3.8-flash"
        assert resp.prompt_tokens == 100
        assert resp.completion_tokens == 20
        assert resp.total_tokens == 120
        assert resp.reasoning_tokens == 10
        assert resp.finish_reason == "end_turn"

        env_passed = mock_exec.call_args.kwargs.get("env")
        assert env_passed is not None
        assert "GITHUB_TOKEN" not in env_passed
        assert "GH_TOKEN" not in env_passed

    await client.close()
    mock_proc.terminate.assert_called_once()
