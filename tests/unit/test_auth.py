"""Tests for authentication."""

import pytest
from craft_dashboard.auth import verify_admin_token
from fastapi import HTTPException


class TestVerifyAdminToken:
    """Tests for verify_admin_token."""

    def test_valid_token(self) -> None:
        """Valid token passes without error."""
        verify_admin_token(token="Bearer correct-token", admin_token="correct-token")

    def test_invalid_token_raises(self) -> None:
        """Invalid token raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(token="Bearer wrong-token", admin_token="correct-token")
        assert exc_info.value.status_code == 401

    def test_missing_bearer_prefix_raises(self) -> None:
        """Token without 'Bearer ' prefix raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(token="correct-token", admin_token="correct-token")
        assert exc_info.value.status_code == 401

    def test_empty_admin_token_raises(self) -> None:
        """Empty admin token always rejects (misconfiguration guard)."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(token="Bearer anything", admin_token="")
        assert exc_info.value.status_code == 401
