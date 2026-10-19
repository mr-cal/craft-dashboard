"""add eval_api_tokens and evaluated_by

Revision ID: 444063d1b5b0
Revises: b7c1d9e42f08
Create Date: 2026-10-15 14:48:17.142571

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '444063d1b5b0'
down_revision: Union[str, None] = 'b7c1d9e42f08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create eval_api_tokens table, add evaluated_by, seed keys, and backfill history."""
    op.create_table(
        "eval_api_tokens",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column(
            "permissions",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_eval_api_tokens_token_hash"),
        "eval_api_tokens",
        ["token_hash"],
        unique=True,
    )

    op.add_column(
        "llm_evaluations",
        sa.Column("evaluated_by", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_llm_evaluations_evaluated_by_eval_api_tokens",
        "llm_evaluations",
        "eval_api_tokens",
        ["evaluated_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_llm_evaluations_evaluated_by"),
        "llm_evaluations",
        ["evaluated_by"],
    )

    import hashlib
    import os
    import secrets

    existing_token = os.environ.get("CRAFT_EVAL_TOKEN") or os.environ.get("EVAL_API_TOKEN")
    if not existing_token:
        try:
            from craft_dashboard.settings import Settings

            existing_token = Settings().eval_api_token
        except Exception:
            existing_token = None

    if existing_token:
        mr_cal_hash = hashlib.sha256(existing_token.encode("utf-8")).hexdigest()
        mr_cal_prefix = (existing_token[:8] + "...") if len(existing_token) >= 8 else "cde_legacy"
    else:
        seed_token = secrets.token_hex(32)
        mr_cal_hash = hashlib.sha256(seed_token.encode("utf-8")).hexdigest()
        mr_cal_prefix = seed_token[:8] + "..."

    server_token = secrets.token_hex(32)
    server_hash = hashlib.sha256(server_token.encode("utf-8")).hexdigest()
    server_prefix = server_token[:8] + "..."

    eval_tokens_table = sa.table(
        "eval_api_tokens",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("token_hash", sa.String),
        sa.column("token_prefix", sa.String),
        sa.column("permissions", sa.JSON),
        sa.column("is_active", sa.Boolean),
    )

    op.bulk_insert(
        eval_tokens_table,
        [
            {
                "id": "mr-cal-bot",
                "name": "Callahan's Primary Evaluation Worker",
                "token_hash": mr_cal_hash,
                "token_prefix": mr_cal_prefix,
                "permissions": ["eval:write", "eval:admin"],
                "is_active": True,
            },
            {
                "id": "craft-dashboard-eval-bot",
                "name": "Server Evaluation Worker",
                "token_hash": server_hash,
                "token_prefix": server_prefix,
                "permissions": ["eval:write"],
                "is_active": True,
            },
        ],
    )

    # Backfill 100% of historical evaluations to mr-cal-bot
    op.execute("UPDATE llm_evaluations SET evaluated_by = 'mr-cal-bot' WHERE evaluated_by IS NULL")


def downgrade() -> None:
    """Drop eval_api_tokens and evaluated_by column."""
    op.drop_index(
        op.f("ix_llm_evaluations_evaluated_by"),
        table_name="llm_evaluations",
    )
    op.drop_constraint(
        "fk_llm_evaluations_evaluated_by_eval_api_tokens",
        "llm_evaluations",
        type_="foreignkey",
    )
    op.drop_column("llm_evaluations", "evaluated_by")
    op.drop_index(
        op.f("ix_eval_api_tokens_token_hash"),
        table_name="eval_api_tokens",
    )
    op.drop_table("eval_api_tokens")
