#!/usr/bin/env python3
"""Backfill OpenRouter search embeddings for existing issues.

One-time backfill for ``Issue.search_embedding``, embedding each issue's
title+body so semantic issue search has coverage over the entire existing
history. Ongoing updates (for new/changed issues) are handled automatically
by the continuous ``scripts/llm/eval_worker.py`` worker whenever an issue is
(re-)evaluated due to a ``content_hash`` change, so this script only needs to
run once after the migration ships (safe to re-run later, e.g. after a bulk
data import).

Usage:
    uv run scripts/backfill_search_embeddings.py
    uv run scripts/backfill_search_embeddings.py --batch-size 50 --limit 200
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

import click
from dotenv import load_dotenv
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from craft_dashboard.llm.client import OPENROUTER_BASE_URL
from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.models.issue import Issue

logger = logging.getLogger(__name__)


def build_search_embedding_text(title: str, body: str | None) -> str:
    """Return the exact text shape used for Issue.search_embedding.

    Shared with ``scripts/llm/eval_worker.py`` so the backfill and the
    ongoing per-evaluation recomputation always embed the same text shape.
    """
    return f"{title}\n\n{body or ''}"


async def _update_batch(
    *,
    async_session: sessionmaker,
    embedding_client: EmbeddingClient,
    rows: list[tuple[int, str, str | None]],
) -> int:
    """Embed and persist one ordered batch of issue rows."""
    texts = [build_search_embedding_text(title, body) for _, title, body in rows]
    embeddings = await embedding_client.embed_batch(texts, dimensions=1024)

    async with async_session() as session, session.begin():
        for (issue_id, _title, _body), embedding in zip(rows, embeddings, strict=True):
            await session.execute(
                update(Issue)
                .where(Issue.id == issue_id)
                .values(search_embedding=embedding)
            )
    return len(rows)


async def run_backfill(
    *,
    database_url: str,
    openrouter_api_key: str,
    embedding_model: str,
    batch_size: int,
    limit: int,
    dry_run: bool,
) -> None:
    """Backfill search_embedding for all issues."""
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    embedding_client = EmbeddingClient(
        base_url=OPENROUTER_BASE_URL,
        model=embedding_model,
        api_key=openrouter_api_key,
        ca_cert="",
    )

    try:
        processed = 0
        updated = 0
        last_id = 0
        while True:
            remaining = max(0, limit - processed) if limit > 0 else batch_size
            current_batch_size = min(batch_size, remaining) if limit > 0 else batch_size
            if current_batch_size == 0:
                break

            async with async_session() as session:
                stmt = (
                    select(Issue.id, Issue.title, Issue.body)
                    .where(Issue.id > last_id, Issue.search_embedding.is_(None))
                    .order_by(Issue.id)
                    .limit(current_batch_size)
                )
                result = await session.execute(stmt)
                rows = [(row[0], row[1], row[2]) for row in result.all()]

            if not rows:
                break

            processed += len(rows)
            start_id = rows[0][0]
            last_id = rows[-1][0]
            logger.info(
                "Processing issues %d-%d (%d rows total so far)",
                start_id,
                last_id,
                processed,
            )

            if not dry_run:
                try:
                    updated += await _update_batch(
                        async_session=async_session,
                        embedding_client=embedding_client,
                        rows=rows,
                    )
                except Exception as exc:
                    logger.warning(
                        "Batch %d-%d failed (%s); falling back to per-row updates",
                        start_id,
                        last_id,
                        exc,
                    )
                    for issue_id, title, body in rows:
                        embedding = await embedding_client.embed(
                            build_search_embedding_text(title, body), dimensions=1024
                        )
                        async with async_session() as session, session.begin():
                            await session.execute(
                                update(Issue)
                                .where(Issue.id == issue_id)
                                .values(search_embedding=embedding)
                            )
                        updated += 1

            logger.info("Progress: processed=%d updated=%d", processed, updated)

        if dry_run:
            logger.info("Dry run: %d issues would be refreshed", processed)
        else:
            logger.info("Done: refreshed %d issue search embeddings", updated)
    finally:
        await embedding_client.close()
        await engine.dispose()


@click.command()
@click.option(
    "--database-url",
    required=True,
    envvar="DATABASE_URL",
    help="Async SQLAlchemy database URL [env: DATABASE_URL]",
)
@click.option(
    "--openrouter-api-key",
    required=True,
    envvar="OPENROUTER_API_KEY",
    help="OpenRouter API key [env: OPENROUTER_API_KEY]",
)
@click.option(
    "--embedding-model",
    default="openai/text-embedding-3-small",
    show_default=True,
    help="OpenRouter embedding model to use for the backfill",
)
@click.option(
    "--batch-size",
    default=100,
    show_default=True,
    type=click.IntRange(min=1),
    help="Rows to process per embedding batch",
)
@click.option(
    "--limit",
    default=0,
    show_default=True,
    type=click.IntRange(min=0),
    help="Max rows to process (0=unlimited)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show how many rows would be updated without making changes",
)
def main(
    database_url: str,
    openrouter_api_key: str,
    embedding_model: str,
    batch_size: int,
    limit: int,
    dry_run: bool,
) -> None:
    """Compute search_embedding for all issues that don't have one yet."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(
        run_backfill(
            database_url=database_url,
            openrouter_api_key=openrouter_api_key,
            embedding_model=embedding_model,
            batch_size=batch_size,
            limit=limit,
            dry_run=dry_run,
        )
    )


if __name__ == "__main__":
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
    main()
