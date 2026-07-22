#!/usr/bin/env python3
"""Backfill OpenRouter summary embeddings for existing LLM evaluations.

Recomputes embeddings for every evaluation row with a summary so the entire
history lives in the same vector space as the continuous ``evaluate`` worker.
This script talks directly to the database and is safe to re-run.

Usage:
    uv run scripts/backfill_embeddings.py
    uv run scripts/backfill_embeddings.py --batch-size 50 --limit 200
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
from craft_dashboard.models.llm_evaluation import LLMEvaluation

logger = logging.getLogger(__name__)


def _build_embedding_text(title: str, summary: str) -> str:
    """Return the exact text shape used by the continuous evaluation worker."""
    return f"{title}. {summary}"


async def _update_batch(
    *,
    async_session: sessionmaker,
    embedding_client: EmbeddingClient,
    rows: list[tuple[int, str, str]],
) -> int:
    """Embed and persist one ordered batch of evaluation rows."""
    texts = [_build_embedding_text(title, summary) for _, title, summary in rows]
    embeddings = await embedding_client.embed_batch(texts, dimensions=1024)

    async with async_session() as session, session.begin():
        for (evaluation_id, _title, _summary), embedding in zip(
            rows, embeddings, strict=True
        ):
            await session.execute(
                update(LLMEvaluation)
                .where(LLMEvaluation.id == evaluation_id)
                .values(summary_embedding=embedding)
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
    """Backfill embeddings for all evaluations with a stored summary."""
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
                    select(LLMEvaluation.id, Issue.title, LLMEvaluation.summary)
                    .join(Issue, LLMEvaluation.issue_id == Issue.id)
                    .where(
                        LLMEvaluation.id > last_id,
                        LLMEvaluation.summary.is_not(None),
                        LLMEvaluation.summary != "",
                    )
                    .order_by(LLMEvaluation.id)
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
                "Processing evaluations %d-%d (%d rows total so far)",
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
                    for evaluation_id, title, summary in rows:
                        embedding = await embedding_client.embed(
                            _build_embedding_text(title, summary), dimensions=1024
                        )
                        async with async_session() as session, session.begin():
                            await session.execute(
                                update(LLMEvaluation)
                                .where(LLMEvaluation.id == evaluation_id)
                                .values(summary_embedding=embedding)
                            )
                        updated += 1

            logger.info("Progress: processed=%d updated=%d", processed, updated)

        if dry_run:
            logger.info("Dry run: %d evaluations would be refreshed", processed)
        else:
            logger.info("Done: refreshed %d evaluation embeddings", updated)
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
    """Recompute summary embeddings for all stored LLM evaluations."""
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
