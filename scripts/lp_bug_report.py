#!/usr/bin/env python3
"""Print a report of Launchpad bug authors sorted by bug count.

Usage:
    DATABASE_URL=postgresql://... uv run scripts/lp_bug_report.py

Environment variables:
    DATABASE_URL: PostgreSQL connection URL
"""

import os
import sys

import sqlalchemy as sa


def main() -> None:
    """Print Launchpad bug authors sorted by bug count."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is required", file=sys.stderr)
        sys.exit(1)

    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                sa.text(
                    """
                    SELECT author, COUNT(*) AS bug_count
                    FROM issues
                    WHERE source = 'launchpad'
                    GROUP BY author
                    ORDER BY bug_count DESC
                    """
                )
            )
            rows = result.fetchall()
    except sa.exc.SQLAlchemyError as exc:
        print(f"ERROR: Database query failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        engine.dispose()

    if not rows:
        return

    print("LP Bug Authors Report (sorted by bug count):")
    for author, count in rows:
        print(f"{author}, {count}")


if __name__ == "__main__":
    main()
