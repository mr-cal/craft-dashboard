"""Discourse forum activity models (see plans/33-forum-activity-tracker.md)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base

__all__ = ["ForumBackfillState", "ForumTopic"]


class ForumTopic(Base):
    """A topic on a tracked Discourse forum, with topic-level metadata only.

    This intentionally stores topic-level aggregates (post/like counts,
    category, timestamps) rather than individual post bodies — see the
    storage feasibility analysis in plans/33-forum-activity-tracker.md
    Step 1 for why that's sufficient (and negligible in size) for the
    current trend-graph requirements.
    """

    __tablename__ = "forum_topics"
    __table_args__ = (UniqueConstraint("forum", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Forum key from craft-dashboard.toml's [forums.*] sections, e.g.
    #: "snapcraft", "charmcraft", "rockcraft".
    forum: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    #: Discourse category slug the topic belongs to — every category on the
    #: forum is tracked and this is what backs the Engagement page's
    #: per-category checkbox filter.
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    #: Discourse's own topic id.
    external_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    posts_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
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
    This cached category list backs the Engagement page's per-category
    checkbox filter panel.

    Historical backfill walks each category's topic list (GET
    /c/{slug}/{id}.json?order=created), which Discourse paginates reliably
    (unlike /search.json — see plans/33-forum-activity-tracker.md's
    "Search API pagination is unreliable" note). ``category_progress``
    tracks a resumable per-category cursor so a multi-thousand-topic
    backfill can spread across several scheduled runs without re-fetching
    pages it already covered.
    """

    __tablename__ = "forum_backfill_state"
    __table_args__ = (UniqueConstraint("forum"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    forum: Mapped[str] = mapped_column(String(50), nullable=False)
    #: Per-category backfill cursor: {category_id (str): {"next_page": int,
    #: "done": bool}}. "done" means either the category's oldest topic page
    #: was reached (more_topics_url is null) or a topic older than the
    #: configured years-lookback cutoff was seen.
    category_progress: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
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
        return f"<ForumBackfillState(forum={self.forum!r}, category_progress={self.category_progress!r})>"
