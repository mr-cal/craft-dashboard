"""Unit tests for scripts/mint_bot_token.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import jwt
import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from scripts.mint_bot_token import (
    generate_jwt,
    main,
    mint_installation_token,
    read_cached_token,
    save_cached_token,
)


@pytest.fixture(scope="module")
def rsa_keys() -> tuple[str, str]:
    """Generate a test RSA key pair (private PEM, public PEM)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    return private_pem, public_pem


def test_generate_jwt(rsa_keys: tuple[str, str]) -> None:
    """Test generating and decoding RS256 JWT."""
    private_pem, public_pem = rsa_keys
    app_id = 987654

    token = generate_jwt(app_id, private_pem)
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])

    assert decoded["iss"] == "987654"
    assert "iat" in decoded
    assert "exp" in decoded
    assert decoded["exp"] > decoded["iat"]


def test_cache_save_and_read(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test saving and retrieving valid token from cache."""
    cache_dir = tmp_path / ".local" / "token_cache"
    monkeypatch.setattr(
        "scripts.mint_bot_token.get_cache_path",
        lambda inst_id, repo: cache_dir / f"{inst_id}_{repo}.json",
    )

    future_exp = (
        (datetime.now(UTC) + timedelta(minutes=45)).isoformat().replace("+00:00", "Z")
    )
    save_cached_token(
        12345,
        "craft-dashboard",
        {"token": "ghs_cached_token", "expires_at": future_exp},
    )

    retrieved = read_cached_token(12345, "craft-dashboard")
    assert retrieved == "ghs_cached_token"


def test_cache_expired_returns_none(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that expired cached token returns None."""
    cache_dir = tmp_path / ".local" / "token_cache"
    monkeypatch.setattr(
        "scripts.mint_bot_token.get_cache_path",
        lambda inst_id, repo: cache_dir / f"{inst_id}_{repo}.json",
    )

    past_exp = (
        (datetime.now(UTC) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    )
    save_cached_token(
        12345,
        "craft-dashboard",
        {"token": "ghs_old_token", "expires_at": past_exp},
    )

    assert read_cached_token(12345, "craft-dashboard") is None


def test_mint_installation_token_success(rsa_keys: tuple[str, str]) -> None:
    """Test minting token via GitHub API exchange with repo downscoping."""
    private_pem, _ = rsa_keys
    fake_response_data = {
        "token": "ghs_fresh_minted_123",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "permissions": {"contents": "write"},
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_response_data
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = mint_installation_token(
            app_id=123,
            installation_id=456,
            private_key_pem=private_pem,
            repo="craft-dashboard",
            use_cache=False,
        )

        assert result["token"] == "ghs_fresh_minted_123"
        url = mock_post.call_args[0][0]
        kwargs = mock_post.call_args[1]
        assert url == "https://api.github.com/app/installations/456/access_tokens"
        assert kwargs["headers"]["Authorization"].startswith("Bearer ")
        assert kwargs["json"] == {"repositories": ["craft-dashboard"]}


def test_mint_installation_token_http_error(rsa_keys: tuple[str, str]) -> None:
    """Test handling GitHub API HTTP error."""
    private_pem, _ = rsa_keys
    mock_request = httpx.Request(
        "POST", "https://api.github.com/app/installations/456/access_tokens"
    )
    mock_response = httpx.Response(
        403,
        request=mock_request,
        text='{"message":"Resource not accessible by integration"}',
    )
    http_error = httpx.HTTPStatusError(
        "Forbidden", request=mock_request, response=mock_response
    )

    with patch("httpx.post", side_effect=http_error):
        with pytest.raises(RuntimeError, match="GitHub API error 403"):
            mint_installation_token(
                app_id=123,
                installation_id=456,
                private_key_pem=private_pem,
                repo="unauthorized-repo",
                use_cache=False,
            )


def test_cli_with_app_credentials(
    rsa_keys: tuple[str, str], tmp_path: pytest.TempPathFactory
) -> None:
    """Test CLI invoking token minting with app credentials."""
    private_pem, _ = rsa_keys
    key_file = tmp_path / "app.pem"
    key_file.write_text(private_pem)

    fake_response = {
        "token": "ghs_cli_token_456",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_response
    mock_resp.raise_for_status.return_value = None

    runner = CliRunner()
    with patch("httpx.post", return_value=mock_resp):
        # Test --print-remote-url
        result = runner.invoke(
            main,
            [
                "--app-id",
                "123",
                "--installation-id",
                "456",
                "--key-path",
                str(key_file),
                "--repo",
                "craft-dashboard",
                "--print-remote-url",
                "--no-cache",
            ],
            env={},
        )
        assert result.exit_code == 0
        assert (
            result.output.strip()
            == "https://x-access-token:ghs_cli_token_456@github.com/mr-cal/craft-dashboard.git"
        )

        # Test --check
        result_check = runner.invoke(
            main,
            [
                "--app-id",
                "123",
                "--installation-id",
                "456",
                "--key-path",
                str(key_file),
                "--repo",
                "vps-infra",
                "--check",
                "--no-cache",
            ],
            env={},
        )
        assert result_check.exit_code == 0
        assert (
            "OK: Successfully generated installation token scoped to vps-infra"
            in result_check.output
        )


def test_cli_fallback_to_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI fallback when GITHUB_APP_* not set but GH_TOKEN is present."""
    runner = CliRunner()
    with patch("scripts.mint_bot_token.load_env_files"):
        result = runner.invoke(
            main,
            ["--print-remote-url", "--repo", "craft-dashboard"],
            env={"GH_TOKEN": "ghp_classic_token_123"},
        )
        assert result.exit_code == 0
        assert (
            result.output.strip()
            == "https://ghp_classic_token_123@github.com/mr-cal/craft-dashboard.git"
        )


def test_cli_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI error output when neither App credentials nor PAT are provided."""
    runner = CliRunner()
    with patch("scripts.mint_bot_token.load_env_files"):
        result = runner.invoke(
            main, ["--check"], env={"GH_TOKEN": "", "GITHUB_TOKEN": ""}
        )
        assert result.exit_code == 1
        assert "Missing required GitHub App credentials" in result.output
