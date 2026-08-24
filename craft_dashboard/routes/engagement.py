"""Engagement routes: Discourse forum activity trend graphs.

See plans/33-forum-activity-tracker.md for the full design.
"""

import json
from collections import defaultdict
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from craft_dashboard.config import DashboardConfig
from craft_dashboard.dependencies import get_config, get_db_session
from craft_dashboard.models.forum import ForumTag, ForumTopic

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/engagement")


@router.get("/forums", response_class=HTMLResponse)
async def forums_page(
    request: Request,
    config: DashboardConfig = Depends(get_config),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the forum activity page with one Chart.js graph per forum."""
    templates: Jinja2Templates = request.app.state.templates

    forums: list[dict] = []
    for name, forum_config in config.forums.items():
        tag_result = await session.execute(
            select(ForumTag.tag_name)
            .where(ForumTag.forum == name)
            .order_by(ForumTag.tag_name)
        )
        tags = [row.tag_name for row in tag_result]
        forums.append(
            {
                "name": name,
                "base_url": forum_config.base_url,
                "tags": tags,
                "default_tags": list(forum_config.default_tags),
            }
        )

    return templates.TemplateResponse(
        request,
        "engagement/forums.html",
        {"forums": forums, "forums_json": json.dumps(forums)},
    )


@router.get("/forums/data", response_class=JSONResponse)
async def forums_data(
    session: AsyncSession = Depends(get_db_session),
    forum: str = Query(...),
) -> JSONResponse:
    """Return monthly post-count series for a forum, one per tag plus "all".

    Buckets every topic by the month it was created in and sums
    ``posts_count`` (the topic's total reply count as of the last
    collector run) into that month — matching the topic-level-aggregate
    storage model described in plans/33-forum-activity-tracker.md (no
    individual post bodies/timestamps are stored). Returns every tag's
    series in one response so the frontend can toggle visibility locally
    without a round trip per checkbox change.
    """
    exists = await session.scalar(
        select(ForumTag.id).where(ForumTag.forum == forum).limit(1)
    )
    if exists is None:
        exists = await session.scalar(
            select(ForumTopic.id).where(ForumTopic.forum == forum).limit(1)
        )
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Forum '{forum}' not found")

    result = await session.execute(
        select(ForumTopic.created_at, ForumTopic.posts_count, ForumTopic.tags).where(
            ForumTopic.forum == forum
        )
    )

    all_by_month: dict[str, int] = defaultdict(int)
    tag_by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in result:
        month_key = row.created_at.strftime("%Y-%m")
        all_by_month[month_key] += row.posts_count
        for tag in row.tags or []:
            tag_by_month[tag][month_key] += row.posts_count

    months = sorted(all_by_month)
    all_series = [all_by_month[m] for m in months]
    tags_series = {
        tag: [by_month.get(m, 0) for m in months]
        for tag, by_month in sorted(tag_by_month.items())
    }

    return JSONResponse({"months": months, "all": all_series, "tags": tags_series})
