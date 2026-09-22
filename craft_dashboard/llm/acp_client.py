"""GitHub Copilot CLI ACP (Agent Client Protocol) client."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
from typing import TYPE_CHECKING, Any

from craft_dashboard.llm.client import (
    LLMResponse,
    _parse_tool_calls_from_content,
)
from craft_dashboard.llm.exceptions import LLMUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class CopilotACPClient:
    """LLM client powered by GitHub Copilot CLI via Agent Client Protocol (ACP).

    Communicates with `gh copilot -- --acp` over stdio JSON-RPC 2.0. Supports
    session multiplexing and parallel prompts over a shared ACP process.
    """

    def __init__(
        self,
        *,
        copilot_cmd: list[str] | None = None,
        cwd: str | None = None,
        timeout: float = 600.0,
    ) -> None:
        self.copilot_cmd = copilot_cmd or ["gh", "copilot", "--", "--acp"]
        self.cwd = cwd or tempfile.gettempdir()
        self.timeout = timeout
        self.retry_callback: Callable[[int, int], None] | None = None

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._pending_requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._session_chunks: dict[str, list[str]] = {}
        self._session_thoughts: dict[str, list[str]] = {}
        self._initialized = False

    async def _ensure_started(self) -> None:
        """Start the ACP server subprocess and run initialization if needed."""
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None:
                return

            bin_name = self.copilot_cmd[0]
            if not shutil.which(bin_name):
                raise LLMUnavailableError(
                    f"Command '{bin_name}' not found. Please install GitHub CLI (gh)."
                )

            logger.info("Starting Copilot ACP process: %s", " ".join(self.copilot_cmd))
            try:
                # Filter out GITHUB_TOKEN / GH_TOKEN when spawning gh copilot.
                # If set (e.g. from repo .env for GitHub API collector), gh uses
                # it instead of the user's stored OAuth credentials in hosts.yml,
                # causing Copilot authentication to fail if that token lacks
                # Copilot permissions.
                spawn_env = dict(os.environ)
                spawn_env.pop("GITHUB_TOKEN", None)
                spawn_env.pop("GH_TOKEN", None)

                self._proc = await asyncio.create_subprocess_exec(
                    *self.copilot_cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=spawn_env,
                )
            except Exception as exc:
                raise LLMUnavailableError(
                    f"Failed to start Copilot ACP server: {exc}"
                ) from exc

            self._reader_task = asyncio.create_task(self._read_stdout())
            self._initialized = False

            # Handshake
            self._req_id += 1
            req_id = self._req_id

            loop = asyncio.get_running_loop()
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending_requests[req_id] = future

            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            }
            payload = json.dumps(msg) + "\n"
            if self._proc.stdin is None:
                raise LLMUnavailableError("Copilot ACP process stdin unavailable")
            self._proc.stdin.write(payload.encode("utf-8"))
            await self._proc.stdin.drain()

            try:
                init_resp = await asyncio.wait_for(future, timeout=self.timeout)
            except TimeoutError as exc:
                self._pending_requests.pop(req_id, None)
                raise LLMUnavailableError("Timeout waiting for ACP initialize") from exc
            except Exception:
                self._pending_requests.pop(req_id, None)
                raise

            if "error" in init_resp:
                await self.close()
                raise LLMUnavailableError(
                    f"ACP initialization failed: {init_resp['error']}"
                )
            self._initialized = True

    async def _read_stdout(self) -> None:
        """Continuously read newline-delimited JSON-RPC messages from stdout."""
        if self._proc is None or self._proc.stdout is None:
            return

        while True:
            try:
                line_bytes = await self._proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8").strip()
                if not line:
                    continue
                data = json.loads(line)
            except (asyncio.CancelledError, GeneratorExit):
                break
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.debug("Failed to decode ACP stdout line", exc_info=True)
                continue

            req_id = data.get("id")
            if req_id is not None and req_id in self._pending_requests:
                future = self._pending_requests.pop(req_id)
                if not future.done():
                    future.set_result(data)
            elif data.get("method") == "session/update":
                params = data.get("params", {})
                sess_id = params.get("sessionId")
                update = params.get("update", {})
                up_type = update.get("sessionUpdate")

                if sess_id:
                    if up_type == "agent_message_chunk":
                        chunk = update.get("content", {}).get("text", "")
                        self._session_chunks.setdefault(sess_id, []).append(chunk)
                    elif up_type == "agent_thought_chunk":
                        chunk = update.get("content", {}).get("text", "")
                        self._session_thoughts.setdefault(sess_id, []).append(chunk)

    async def _send_request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for the matching response."""
        if self._proc is None or self._proc.stdin is None:
            raise LLMUnavailableError("Copilot ACP process is not running")

        async with self._lock:
            self._req_id += 1
            req_id = self._req_id

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[req_id] = future

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        payload = json.dumps(msg) + "\n"

        try:
            self._proc.stdin.write(payload.encode("utf-8"))
            await self._proc.stdin.drain()
            return await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError as exc:
            self._pending_requests.pop(req_id, None)
            raise LLMUnavailableError(
                f"Timeout waiting for ACP response to method '{method}'"
            ) from exc
        except Exception:
            self._pending_requests.pop(req_id, None)
            raise

    def _format_messages_to_prompt(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: dict | None = None,
    ) -> str:
        """Format OpenAI-style messages and tool/schema instructions into ACP prompt text."""
        parts: list[str] = []

        if response_format and response_format.get("type") == "json_object":
            parts.append(
                "SYSTEM INSTRUCTION: You MUST output your final answer as valid JSON only. "
                "Do NOT wrap it in extra conversational text."
            )

        if tools:
            tool_descriptions = json.dumps(tools, indent=2)
            parts.append(
                "SYSTEM INSTRUCTION: You have access to the following functions:\n"
                f"{tool_descriptions}\n"
                "If you decide to invoke any function, output tool calls in the format:\n"
                '<tool_call>{"name": "<function_name>", "arguments": {<arguments>}}</tool_call>\n'
                "or execute tool calls if supported."
            )

        for msg in messages:
            role = msg.get("role", "user").upper()
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    content += f"\n<tool_call>{json.dumps(fn)}</tool_call>"
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id:
                parts.append(f"TOOL RESULT ({tool_call_id}):\n{content}")
            else:
                parts.append(f"{role}:\n{content}")

        return "\n\n".join(parts)

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,  # noqa: ARG002
        max_tokens: int = 1024,  # noqa: ARG002
        response_format: dict | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,  # noqa: ARG002
    ) -> LLMResponse:
        """Execute a prompt via ACP and return an LLMResponse."""
        await self._ensure_started()

        # Create session
        sess_resp = await self._send_request(
            "session/new",
            {"cwd": self.cwd, "mcpServers": []},
        )
        if "error" in sess_resp:
            raise LLMUnavailableError(
                f"Failed to create ACP session: {sess_resp['error']}"
            )

        sess_id = sess_resp["result"]["sessionId"]
        self._session_chunks[sess_id] = []
        self._session_thoughts[sess_id] = []

        try:
            # Configure model and allow_all
            await self._send_request(
                "session/set_config_option",
                {"sessionId": sess_id, "configId": "model", "value": model},
            )
            await self._send_request(
                "session/set_config_option",
                {"sessionId": sess_id, "configId": "allow_all", "value": "on"},
            )

            # Build prompt
            prompt_text = self._format_messages_to_prompt(
                messages, tools=tools, response_format=response_format
            )

            prompt_resp = await self._send_request(
                "session/prompt",
                {
                    "sessionId": sess_id,
                    "prompt": [{"type": "text", "text": prompt_text}],
                },
            )

            if "error" in prompt_resp:
                raise LLMUnavailableError(f"ACP prompt failed: {prompt_resp['error']}")

            res = prompt_resp.get("result", {})
            usage = res.get("usage", {})
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)
            total_tokens = usage.get("totalTokens", 0)
            reasoning_tokens = usage.get("thoughtTokens", 0)

            content = "".join(self._session_chunks.get(sess_id, []))
            reasoning = "".join(self._session_thoughts.get(sess_id, [])) or None

            tool_calls = None
            if content:
                parsed_tools, remaining_content = _parse_tool_calls_from_content(
                    content
                )
                if parsed_tools:
                    tool_calls = parsed_tools
                    content = remaining_content

            return LLMResponse(
                content=content,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=total_tokens,
                model=model,
                cost_usd=None,
                tool_calls=tool_calls,
                reasoning=reasoning,
                finish_reason=res.get("stopReason"),
                reasoning_tokens=reasoning_tokens,
            )
        finally:
            self._session_chunks.pop(sess_id, None)
            self._session_thoughts.pop(sess_id, None)

    async def check_quota(self) -> None:
        """ACP does not have a quota check API; returns successfully."""
        return

    async def close(self) -> None:
        """Terminate the ACP subprocess and cleanup."""
        async with self._lock:
            if self._reader_task is not None:
                self._reader_task.cancel()
                self._reader_task = None

            if self._proc is not None:
                if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                    self._proc.stdin.close()
                    with contextlib.suppress(OSError):
                        await self._proc.stdin.wait_closed()
                if self._proc.returncode is None:
                    try:
                        self._proc.terminate()
                        await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                    except (TimeoutError, ProcessLookupError, OSError):
                        self._proc.kill()
                        with contextlib.suppress(OSError):
                            await self._proc.wait()
                # Yield to the event loop so transport connection-lost callbacks can run
                # while the event loop is still open
                await asyncio.sleep(0.05)
                self._proc = None

            for fut in self._pending_requests.values():
                if not fut.done():
                    fut.cancel()
            self._pending_requests.clear()
            self._session_chunks.clear()
            self._session_thoughts.clear()
            self._initialized = False
