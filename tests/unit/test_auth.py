"""Tests for authentication."""

import pytest
from craft_dashboard.auth import verify_admin_token
from fastapi import HTTPException

_CORRECT_TOKEN = "correct-token"
_WRONG_TOKEN = "wrong-token"
_TEST_TOKEN = "test"


class TestVerifyAdminToken:
    """Tests for verify_admin_token."""

    def test_valid_token(self) -> None:
        """Valid token passes without error."""
        verify_admin_token(token=f"Bearer {_CORRECT_TOKEN}", admin_token=_CORRECT_TOKEN)

    def test_invalid_token_raises(self) -> None:
        """Invalid token raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(
                token=f"Bearer {_WRONG_TOKEN}", admin_token=_CORRECT_TOKEN
            )
        assert exc_info.value.status_code == 401

    def test_missing_bearer_prefix_raises(self) -> None:
        """Token without 'Bearer ' prefix raises HTTPException 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(token=_CORRECT_TOKEN, admin_token=_CORRECT_TOKEN)
        assert exc_info.value.status_code == 401

    def test_empty_admin_token_raises(self) -> None:
        """Empty admin token always rejects (misconfiguration guard)."""
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(token=f"Bearer {_TEST_TOKEN}", admin_token="")
        assert exc_info.value.status_code == 401
