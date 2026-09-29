"""Alembic migration environment configuration."""

import os
from logging.config import fileConfig

from alembic import context
from craft_dashboard.models import Base
from sqlalchemy import create_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: Indexes created directly by migrations because SQLAlchemy metadata cannot
#: express them: pgvector ANN indexes need an access method and operator class.
#: Autogenerate cannot see them in the models, so without this filter every
#: `alembic check` and `--autogenerate` run proposes dropping them.
UNMANAGED_INDEXES = frozenset(
    {
        "ix_issues_search_embedding",
        "ix_llm_evaluations_embedding",
        "ix_issues_collection_run_id",
    }
)


def include_object(_object, name, type_, reflected, _compare_to):  # noqa: ANN001, ANN202
    """Exclude migration-managed objects from autogenerate comparison."""
    if type_ == "index" and reflected and name in UNMANAGED_INDEXES:
        return False
    return True


# Use synchronous URL for migrations (replace asyncpg with psycopg2)
database_url = os.environ.get("DATABASE_URL", "postgresql://localhost/craft_dashboard")
sync_url = database_url.replace("+asyncpg", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=sync_url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transaction_per_migration=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(sync_url)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
