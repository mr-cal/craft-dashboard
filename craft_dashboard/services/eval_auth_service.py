"""Service for evaluation token authentication, permission validation, and caching."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from craft_dashboard.repositories.eval_token_repository import EvalTokenRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

CACHE_TTL_SECONDS = 60.0
LAST_USED_UPDATE_INTERVAL = timedelta(minutes=5)


class InvalidTokenError(Exception):
    """Raised when an evaluation token is missing, malformed, invalid, or revoked."""


class InsufficientPermissionsError(Exception):
    """Raised when an evaluation token lacks the required permission scope."""


@dataclass(frozen=True)
class AuthenticatedToken:
    """Authenticated evaluation token identity and permissions."""

    id: str
    name: str
    permissions: tuple[str, ...]
    is_active: bool


# Thread-safe in-memory cache mapping token_hash -> (AuthenticatedToken, cached_at_monotonic)
_token_cache: dict[str, tuple[AuthenticatedToken, float]] = {}


def clear_eval_token_cache() -> None:
    """Clear all cached evaluation tokens."""
    _token_cache.clear()


def evict_eval_token(token_id: str) -> None:
    """Evict any cached entries matching the specified token ID."""
    to_delete = [
        token_hash
        for token_hash, (entry, _) in _token_cache.items()
        if entry.id == token_id
    ]
    for token_hash in to_delete:
        _token_cache.pop(token_hash, None)


class EvalAuthService:
    """Business logic for evaluation token authentication and permission verification."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the eval auth service with database session."""
        self.session = session
        self.repository = EvalTokenRepository(session)

    @staticmethod
    def hash_token(raw_token: str) -> str:
        """Compute SHA-256 digest of secret token string."""
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    async def authenticate_token(self, authorization_header: str) -> AuthenticatedToken:
        """Verify bearer token from Authorization header against eval_api_tokens.

        Uses an in-memory 60s TTL cache to prevent database bottlenecks during
        high-frequency evaluation polling.
        """
        if not authorization_header or not authorization_header.startswith("Bearer "):
            raise InvalidTokenError(
                "Missing or malformed Authorization header. Use 'Bearer <token>'."
            )

        raw_token = authorization_header.removeprefix("Bearer ").strip()
        if not raw_token:
            raise InvalidTokenError("Empty bearer token provided.")

        token_hash = self.hash_token(raw_token)
        now_monotonic = time.monotonic()

        cached = _token_cache.get(token_hash)
        if cached:
            auth_token, cached_at = cached
            if now_monotonic - cached_at < CACHE_TTL_SECONDS:
                if not auth_token.is_active:
                    raise InvalidTokenError("Evaluation token has been revoked.")
                return auth_token

        token_record = await self.repository.get_by_hash(token_hash)
        if not token_record or not token_record.is_active:
            raise InvalidTokenError("Invalid or revoked eval API token.")

        auth_token = AuthenticatedToken(
            id=token_record.id,
            name=token_record.name,
            permissions=tuple(token_record.permissions or []),
            is_active=token_record.is_active,
        )

        _token_cache[token_hash] = (auth_token, now_monotonic)

        # Throttled update of last_used_at
        now_utc = datetime.now(tz=UTC)
        if (
            token_record.last_used_at is None
            or (now_utc - token_record.last_used_at) > LAST_USED_UPDATE_INTERVAL
        ):
            await self.repository.update_last_used(token_record.id, now_utc)

        return auth_token

    @staticmethod
    def check_permission(auth_token: AuthenticatedToken, permission: str) -> None:
        """Assert that the authenticated token possesses the required permission scope."""
        if (
            permission not in auth_token.permissions
            and "eval:admin" not in auth_token.permissions
        ):
            raise InsufficientPermissionsError(
                f"Token '{auth_token.id}' lacks required permission '{permission}'."
            )

    @staticmethod
    def resolve_clearance_scope(
        auth_token: AuthenticatedToken, requested_evaluated_by: str | None
    ) -> str | None:
        """Enforce clearance scope based on token permissions.

        Admin tokens ('eval:admin') may clear evaluations across any or all keys.
        Non-admin tokens ('eval:write') can only clear evaluations they generated.
        """
        is_admin = "eval:admin" in auth_token.permissions

        if is_admin:
            return requested_evaluated_by

        if requested_evaluated_by and requested_evaluated_by != auth_token.id:
            raise InsufficientPermissionsError(
                f"Non-admin tokens can only clear their own evaluations (token: '{auth_token.id}')."
            )

        return auth_token.id
