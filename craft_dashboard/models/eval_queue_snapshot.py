"""Eval queue depth snapshot model."""

from datetime import datetime

from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class EvalQueueSnapshot(Base):
    """A point-in-time sample of the LLM evaluation queue's depth.

    Recorded (at most once every few minutes — see
    ``craft_dashboard.routes.eval_api._maybe_record_queue_snapshot``) as a
    side effect of the continuous ``evaluate`` worker's own ``/api/eval/next``
    polling, so the admin dashboard can chart queue depth over time without
    needing a separate cron job or sampler process.
    """

    __tablename__ = "eval_queue_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_open: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_today: Mapped[int] = mapped_column(Integer, nullable=False)
