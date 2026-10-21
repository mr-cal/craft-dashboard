"""Unit tests for EvalTokenRepository."""

from datetime import UTC, datetime, timedelta

from craft_dashboard.repositories.eval_token_repository import EvalTokenRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_eval_token, make_evaluation, make_issue, make_project


async def test_create_and_get_by_id(test_db_session: AsyncSession) -> None:
    repo = EvalTokenRepository(test_db_session)
    await repo.create_token(
        token_id="worker-1",
        name="Worker One",
        token_hash="hash1",
        token_prefix="cde_123...",
        permissions=["eval:write"],
    )
    await test_db_session.commit()

    fetched = await repo.get_by_id("worker-1")
    assert fetched is not None
    assert fetched.id == "worker-1"
    assert fetched.name == "Worker One"
    assert fetched.token_hash == "hash1"
    assert fetched.is_active is True


async def test_get_by_hash(test_db_session: AsyncSession) -> None:
    repo = EvalTokenRepository(test_db_session)
    token = make_eval_token(id="worker-2", raw_token="secret-token-123")
    test_db_session.add(token)
    await test_db_session.commit()

    fetched = await repo.get_by_hash(token.token_hash)
    assert fetched is not None
    assert fetched.id == "worker-2"

    missing = await repo.get_by_hash("nonexistent-hash")
    assert missing is None


async def test_revoke_token(test_db_session: AsyncSession) -> None:
    repo = EvalTokenRepository(test_db_session)
    token = make_eval_token(id="worker-revocable")
    test_db_session.add(token)
    await test_db_session.commit()

    revoked = await repo.revoke_token("worker-revocable")
    assert revoked is True
    await test_db_session.commit()

    fetched = await repo.get_by_id("worker-revocable")
    assert fetched is not None
    assert fetched.is_active is False
    assert fetched.revoked_at is not None

    # Revoking already revoked returns False
    second_revoke = await repo.revoke_token("worker-revocable")
    assert second_revoke is False


async def test_list_tokens(test_db_session: AsyncSession) -> None:
    repo = EvalTokenRepository(test_db_session)
    t1 = make_eval_token(id="active-1", raw_token="token-active-1", is_active=True)
    t2 = make_eval_token(id="inactive-1", raw_token="token-inactive-1", is_active=False)
    test_db_session.add_all([t1, t2])
    await test_db_session.commit()

    active_only = await repo.list_tokens(include_revoked=False)
    assert any(t.id == "active-1" for t in active_only)
    assert not any(t.id == "inactive-1" for t in active_only)

    all_tokens = await repo.list_tokens(include_revoked=True)
    assert any(t.id == "active-1" for t in all_tokens)
    assert any(t.id == "inactive-1" for t in all_tokens)


async def test_count_and_delete_evaluations_fine_grained(
    test_db_session: AsyncSession,
) -> None:
    repo = EvalTokenRepository(test_db_session)
    now = datetime.now(tz=UTC)

    p1 = make_project(id=10, name="project-a")
    p2 = make_project(id=20, name="project-b")
    i1 = make_issue(id=100, project_id=10, external_id="100")
    i2 = make_issue(id=200, project_id=20, external_id="200")
    test_db_session.add_all([p1, p2, i1, i2])
    await test_db_session.commit()

    e1 = make_evaluation(
        id=1, issue_id=100, latest=True, evaluated_at=now - timedelta(days=2)
    )
    e1.evaluated_by = "worker-a"

    e2 = make_evaluation(
        id=2, issue_id=100, latest=False, evaluated_at=now - timedelta(days=10)
    )
    e2.evaluated_by = "worker-b"

    e3 = make_evaluation(id=3, issue_id=200, latest=True, evaluated_at=now)
    e3.evaluated_by = "worker-a"

    test_db_session.add_all([e1, e2, e3])
    await test_db_session.commit()

    # Filter by evaluated_by
    count_a = await repo.count_evaluations(evaluated_by="worker-a")
    assert count_a == 2

    # Filter by project and evaluated_by
    count_p1_a = await repo.count_evaluations(
        project="project-a", evaluated_by="worker-a"
    )
    assert count_p1_a == 1

    # Filter by stale_days
    count_stale = await repo.count_evaluations(stale_days=5, now=now)
    assert count_stale == 1  # e2 is 10 days old

    # Delete scoped to worker-b
    deleted = await repo.delete_evaluations(evaluated_by="worker-b")
    assert deleted == 1
    await test_db_session.commit()

    remaining = await repo.count_evaluations()
    assert remaining == 2


async def test_rename_token(test_db_session: AsyncSession) -> None:
    repo = EvalTokenRepository(test_db_session)
    token = make_eval_token(id="worker-rename", name="Old Name")
    test_db_session.add(token)
    await test_db_session.commit()

    renamed = await repo.rename_token("worker-rename", "New Name")
    assert renamed is True
    await test_db_session.commit()

    fetched = await repo.get_by_id("worker-rename")
    assert fetched is not None
    assert fetched.name == "New Name"

    missing = await repo.rename_token("nonexistent", "Name")
    assert missing is False


async def test_rotate_token(test_db_session: AsyncSession) -> None:
    repo = EvalTokenRepository(test_db_session)
    token = make_eval_token(
        id="worker-rotate",
        raw_token="old-token-value",
    )
    test_db_session.add(token)
    await test_db_session.commit()

    rotated = await repo.rotate_token(
        token_id="worker-rotate",
        token_hash="new-hash",
        token_prefix="cde_new...",
    )
    assert rotated is True
    await test_db_session.commit()

    fetched = await repo.get_by_id("worker-rotate")
    assert fetched is not None
    assert fetched.token_hash == "new-hash"
    assert fetched.token_prefix == "cde_new..."
    assert fetched.is_active is True

    missing = await repo.rotate_token(
        token_id="nonexistent",
        token_hash="new-hash",
        token_prefix="cde_new...",
    )
    assert missing is False
