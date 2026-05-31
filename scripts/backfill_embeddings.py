#!/usr/bin/env python3
"""Backfill summary_embedding for existing LLM evaluations that lack one.

Run this after enabling LOCAL_LLM_EMBEDDING_MODEL if you already have
evaluations without embeddings. It reads directly from the database
(requires DB access) and updates rows in batches.

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

from craft_dashboard.llm.embeddings import EmbeddingClient
from craft_dashboard.models.llm_evaluation import LLMEvaluation

logger = logging.getLogger(__name__)


async def run_backfill(
    *,
    database_url: str,
    llm_url: str,
    embedding_model: str,
    llm_api_key: str,
    ca_cert: str,
    batch_size: int,
    limit: int,
    dry_run: bool,
) -> None:
    """Backfill embeddings for evaluations that are missing one."""
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    embedding_client = EmbeddingClient(
        base_url=llm_url.rstrip("/"),
        model=embedding_model,
        api_key=llm_api_key,
        ca_cert=ca_cert,
    )

    try:
        async with async_session() as session:
            stmt = (
                select(LLMEvaluation.id, LLMEvaluation.summary)
                .where(
                    LLMEvaluation.latest.is_(True),
                    LLMEvaluation.summary.isnot(None),
                    LLMEvaluation.summary_embedding.is_(None),
                )
                .order_by(LLMEvaluation.id)
            )
            if limit > 0:
                stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            rows = result.all()

        total = len(rows)
        logger.info("Found %d evaluations needing embeddings", total)
        if dry_run:
            logger.info("Dry run — no changes will be made")
            return

        updated = 0
        for i in range(0, total, batch_size):
            batch = rows[i : i + batch_size]
            logger.info(
                "Processing batch %d-%d of %d...",
                i + 1,
                min(i + batch_size, total),
                total,
            )
            for eval_id, summary in batch:
                try:
                    embedding = await embedding_client.embed(summary)
                except Exception:
                    logger.warning("Failed to embed evaluation %d, skipping", eval_id)
                    continue

                async with async_session() as session, session.begin():
                    await session.execute(
                        update(LLMEvaluation)
                        .where(LLMEvaluation.id == eval_id)
                        .values(summary_embedding=embedding)
                    )
                updated += 1

            logger.info("Progress: %d/%d updated", updated, total)

        logger.info("Done: updated %d evaluations", updated)
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
    "--llm-url",
    default="http://localhost:11434/v1",
    show_default=True,
    envvar="LOCAL_LLM_URL",
    help="OpenAI-compatible LLM endpoint [env: LOCAL_LLM_URL]",
)
@click.option(
    "--embedding-model",
    required=True,
    envvar="LOCAL_LLM_EMBEDDING_MODEL",
    help="Embedding model name [env: LOCAL_LLM_EMBEDDING_MODEL]",
)
@click.option(
    "--llm-api-key",
    default="",
    envvar="LOCAL_LLM_API_KEY",
    help="API key for the LLM endpoint [env: LOCAL_LLM_API_KEY]",
)
@click.option(
    "--ca-cert",
    default="",
    envvar="LOCAL_LLM_CA_CERT",
    help="PEM CA cert path for LLM server TLS verification [env: LOCAL_LLM_CA_CERT]",
)
@click.option(
    "--batch-size",
    default=100,
    show_default=True,
    type=click.IntRange(min=1),
    help="Rows to process per DB transaction",
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
    llm_url: str,
    embedding_model: str,
    llm_api_key: str,
    ca_cert: str,
    batch_size: int,
    limit: int,
    dry_run: bool,
) -> None:
    """Backfill summary_embedding for evaluations that are missing one."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(
        run_backfill(
            database_url=database_url,
            llm_url=llm_url,
            embedding_model=embedding_model,
            llm_api_key=llm_api_key,
            ca_cert=ca_cert,
            batch_size=batch_size,
            limit=limit,
            dry_run=dry_run,
        )
    )


if __name__ == "__main__":
    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
    main()
