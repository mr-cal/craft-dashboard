"""Unit tests for EvalAuthService and evaluation authentication caching."""

import pytest
from craft_dashboard.services.eval_auth_service import (
    AuthenticatedToken,
    EvalAuthService,
    InsufficientPermissionsError,
    InvalidTokenError,
    clear_eval_token_cache,
    evict_eval_token,
)
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_eval_token


@pytest.fixture(autouse=True)
def _reset_token_cache() -> None:
    clear_eval_token_cache()


async def test_authenticate_valid_token(test_db_session: AsyncSession) -> None:
    token = make_eval_token(
        id="worker-valid",
        name="Valid Worker",
        raw_token="secret-token-abc",
        permissions=["eval:write"],
    )
    test_db_session.add(token)
    await test_db_session.commit()

    service = EvalAuthService(test_db_session)
    auth_token = await service.authenticate_token("Bearer secret-token-abc")
    assert auth_token.id == "worker-valid"
    assert "eval:write" in auth_token.permissions
    assert auth_token.is_active is True

    # Cache hit: verify returned object matches
    cached = await service.authenticate_token("Bearer secret-token-abc")
    assert cached.id == auth_token.id


async def test_authenticate_invalid_token(test_db_session: AsyncSession) -> None:
    service = EvalAuthService(test_db_session)
    with pytest.raises(InvalidTokenError, match="Invalid or revoked"):
        await service.authenticate_token("Bearer non-existent-token")


async def test_authenticate_revoked_token(test_db_session: AsyncSession) -> None:
    token = make_eval_token(
        id="worker-revoked",
        raw_token="revoked-token-123",
        is_active=False,
    )
    test_db_session.add(token)
    await test_db_session.commit()

    service = EvalAuthService(test_db_session)
    with pytest.raises(InvalidTokenError, match="Invalid or revoked"):
        await service.authenticate_token("Bearer revoked-token-123")


async def test_authenticate_malformed_header(test_db_session: AsyncSession) -> None:
    service = EvalAuthService(test_db_session)
    with pytest.raises(InvalidTokenError, match="Missing or malformed"):
        await service.authenticate_token("Basic 12345")

    with pytest.raises(InvalidTokenError, match="Missing or malformed"):
        await service.authenticate_token("")


async def test_evict_token_cache(test_db_session: AsyncSession) -> None:
    token = make_eval_token(id="worker-evict", raw_token="token-evict-1")
    test_db_session.add(token)
    await test_db_session.commit()

    service = EvalAuthService(test_db_session)
    await service.authenticate_token("Bearer token-evict-1")

    # Evict and ensure cache is cleared for this ID
    evict_eval_token("worker-evict")

    # Deactivate in database
    token.is_active = False
    await test_db_session.commit()

    # Next call should hit DB and fail
    with pytest.raises(InvalidTokenError):
        await service.authenticate_token("Bearer token-evict-1")


def test_check_permission() -> None:
    worker = AuthenticatedToken(
        id="worker", name="Worker", permissions=("eval:write",), is_active=True
    )
    admin = AuthenticatedToken(
        id="admin",
        name="Admin",
        permissions=("eval:write", "eval:admin"),
        is_active=True,
    )

    # Worker has eval:write
    EvalAuthService.check_permission(worker, "eval:write")

    # Worker lacks eval:admin
    with pytest.raises(InsufficientPermissionsError):
        EvalAuthService.check_permission(worker, "eval:admin")

    # Admin possesses any permission
    EvalAuthService.check_permission(admin, "eval:write")
    EvalAuthService.check_permission(admin, "eval:admin")
    EvalAuthService.check_permission(admin, "custom:scope")


def test_resolve_clearance_scope() -> None:
    worker = AuthenticatedToken(
        id="worker-coworker", name="Worker", permissions=("eval:write",), is_active=True
    )
    admin = AuthenticatedToken(
        id="admin-user",
        name="Admin",
        permissions=("eval:write", "eval:admin"),
        is_active=True,
    )

    # Worker clearing with no scope defaults to their own token ID
    assert EvalAuthService.resolve_clearance_scope(worker, None) == "worker-coworker"

    # Worker clearing explicitly with their own token ID succeeds
    assert (
        EvalAuthService.resolve_clearance_scope(worker, "worker-coworker")
        == "worker-coworker"
    )

    # Worker attempting to clear another token ID raises InsufficientPermissionsError
    with pytest.raises(
        InsufficientPermissionsError,
        match="Non-admin tokens can only clear their own evaluations",
    ):
        EvalAuthService.resolve_clearance_scope(worker, "mr-cal-bot")

    # Admin can clear across all keys (None)
    assert EvalAuthService.resolve_clearance_scope(admin, None) is None

    # Admin can clear any specific key
    assert (
        EvalAuthService.resolve_clearance_scope(admin, "worker-coworker")
        == "worker-coworker"
    )
    assert (
        EvalAuthService.resolve_clearance_scope(admin, "stormclanker") == "stormclanker"
    )
