"""Tests for scripts.llm.eval_http."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from scripts.llm.eval_http import format_error_body, post_submission
from scripts.llm.worker_runtime import Runtime


class TestFormatErrorBody:
    """Tests for format_error_body."""

    def test_json_error_message(self) -> None:
        response = httpx.Response(
            400,
            json={"error": {"message": "Invalid field"}},
            request=httpx.Request("POST", "http://x"),
        )
        assert format_error_body(response) == "Invalid field"

    def test_json_detail_message(self) -> None:
        response = httpx.Response(
            422,
            json={"detail": "Validation error"},
            request=httpx.Request("POST", "http://x"),
        )
        assert format_error_body(response) == "Validation error"

    def test_html_server_error(self) -> None:
        response = httpx.Response(
            500,
            headers={"content-type": "text/html"},
            text="<html>500 Internal Server Error</html>",
            request=httpx.Request("POST", "http://x"),
        )
        assert "server error; check the craft-dashboard logs" in format_error_body(
            response
        )

    def test_empty_body_falls_back_to_reason_phrase(self) -> None:
        response = httpx.Response(
            502,
            text="",
            request=httpx.Request("POST", "http://x"),
        )
        assert format_error_body(response) == "Bad Gateway"

    def test_whitespace_body_falls_back_to_reason_phrase(self) -> None:
        response = httpx.Response(
            503,
            text="   \n  ",
            request=httpx.Request("POST", "http://x"),
        )
        assert format_error_body(response) == "Service Unavailable"


class TestPostSubmission:
    """Tests for post_submission retries."""

    @pytest.mark.asyncio
    async def test_retries_on_500_and_succeeds(self) -> None:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            side_effect=[
                httpx.Response(500, request=httpx.Request("POST", "http://x")),
                httpx.Response(200, request=httpx.Request("POST", "http://x")),
            ]
        )
        runtime = cast(
            Runtime,
            SimpleNamespace(
                http_client=mock_http,
                headers={"Authorization": "Bearer test"},
            ),
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await post_submission(
                runtime, issue_ref="snapcraft#1", submission={"issue_id": 1}
            )

        assert response is not None
        assert response.status_code == 200
        assert mock_http.post.call_count == 2
        mock_sleep.assert_called_once_with(2)

    @pytest.mark.asyncio
    async def test_does_not_retry_on_409(self) -> None:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            return_value=httpx.Response(409, request=httpx.Request("POST", "http://x"))
        )
        runtime = cast(
            Runtime,
            SimpleNamespace(
                http_client=mock_http,
                headers={"Authorization": "Bearer test"},
            ),
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            response = await post_submission(
                runtime, issue_ref="snapcraft#1", submission={"issue_id": 1}
            )

        assert response is not None
        assert response.status_code == 409
        assert mock_http.post.call_count == 1
        mock_sleep.assert_not_called()
