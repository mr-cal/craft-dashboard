"""Discourse forum activity models (see plans/33-forum-activity-tracker.md)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base

__all__ = ["ForumBackfillState", "ForumTag", "ForumTopic"]


class ForumTopic(Base):
    """A topic on a tracked Discourse forum, with topic-level metadata only.

    This intentionally stores topic-level aggregates (post/like counts,
    tags, timestamps) rather than individual post bodies — see the storage
    feasibility analysis in plans/33-forum-activity-tracker.md Step 1 for
    why that's sufficient (and negligible in size) for the current
    trend-graph requirements.
    """

    __tablename__ = "forum_topics"
    __table_args__ = (UniqueConstraint("forum", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Forum key from craft-dashboard.toml's [forums.*] sections, e.g.
    #: "snapcraft", "charmcraft", "rockcraft".
    forum: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    #: Discourse category slug the topic belongs to (informational only —
    #: every category on the forum is tracked, none are filtered out).
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    #: Discourse's own topic id.
    external_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    posts_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<ForumTopic(forum={self.forum!r}, external_id={self.external_id!r}, title={self.title!r})>"


class ForumBackfillState(Base):
    """Per-forum backfill/refresh progress, one row per configured forum.

    Also caches the forum's resolved category list (rather than adding a
    separate table for it) since it's small, single-row-per-forum data that
    only needs a periodic refresh, matching the other cache fields here.
    """

    __tablename__ = "forum_backfill_state"
    __table_args__ = (UniqueConstraint("forum"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    forum: Mapped[str] = mapped_column(String(50), nullable=False)
    #: First day of the earliest month that has been fully backfilled.
    #: None means backfill hasn't started yet.
    earliest_month_backfilled: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When refresh_recent() last completed successfully for this forum.
    last_incremental_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Cached list of category slugs resolved from GET /categories.json.
    categories_cache: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    categories_cached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<ForumBackfillState(forum={self.forum!r}, earliest_month_backfilled={self.earliest_month_backfilled!r})>"


class ForumTag(Base):
    """A tag discovered on a tracked forum, cached from GET /tags.json.

    Backs the dynamic per-tag checkbox filter on the Engagement forum
    activity page (Step 5) — refreshed periodically (e.g. daily) so newly
    created tags appear automatically without a deploy.
    """

    __tablename__ = "forum_tags"
    __table_args__ = (UniqueConstraint("forum", "tag_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    forum: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tag_name: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<ForumTag(forum={self.forum!r}, tag_name={self.tag_name!r})>"
