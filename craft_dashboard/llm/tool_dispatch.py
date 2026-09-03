"""Worker-side dispatch of model tool calls to sandboxed implementations."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from craft_dashboard.git_mirrors import reader
from craft_dashboard.git_mirrors.exceptions import GitMirrorError
from craft_dashboard.git_mirrors.paths import mirror_path_for

if TYPE_CHECKING:
    from pathlib import Path

    from craft_dashboard.llm.embeddings import EmbeddingClient

_GREP_HIT_MIN_FIELDS = 3
#: Cap on how many matches grep_repo/git_log_search will return in a single
#: call, across however many repos were searched. Both tools can otherwise
#: return an unbounded number of hits (bounded only by the 200KB per-repo
#: byte ceiling in git_mirrors.reader) -- a broad pattern searched across
#: many repos was a major driver of ballooning prompt-token growth in the
#: qwen debug-evals runs. Once the cap is hit, a truncation notice tells the
#: model to narrow its pattern/query or the `repos` list instead of paying
#: for (and reading) hundreds of matches it likely doesn't need.
_MAX_MATCHES_PER_CALL = 50


@dataclass
class ToolContext:
    """Context needed to execute one evaluation's tool calls."""

    mirror_dir: Path
    allowed_projects: dict[str, str]
    pinned_shas: dict[str, str]
    eval_server_base_url: str
    eval_api_token: str
    issue_id: int
    default_project: str | None = None
    touched_paths: set[tuple[str, str]] = field(default_factory=set)
    embed_client: EmbeddingClient | None = None

    def record_path(self, project: str, path: str) -> None:
        """Record a touched repo/path pair for reverse-index persistence."""
        owner = self.allowed_projects.get(project)
        repo = f"{owner}/{project}" if owner else project
        self.touched_paths.add((repo, path))


def _resolve_ref(
    ctx: ToolContext, *, project: str, requested_ref: object | None
) -> str:
    """Resolve an explicit ref or fall back to the pinned SHA for project."""
    if requested_ref is not None:
        ref = str(requested_ref)
        if ref not in ctx.pinned_shas.values():
            raise GitMirrorError(f"ref must be one of the pinned SHAs, got: {ref!r}")
        return ref
    try:
        return ctx.pinned_shas[project]
    except KeyError as exc:
        raise GitMirrorError(
            f"No pinned SHA available for project {project!r}"
        ) from exc


def _resolve_projects(ctx: ToolContext, repos: object | None) -> list[str]:
    """Resolve a repo list argument, defaulting to the issue's own project.

    Narrowing the default scope to the issue's own project (rather than all
    allowed projects) makes tool calls faster and more precise for the
    common case. Callers can still pass an explicit `repos` list to search
    across other projects, e.g. snapcraft-rocks acting as an "app" that
    needs visibility into other apps/libraries.
    """
    if repos is None:
        if ctx.default_project is not None:
            return [ctx.default_project]
        return list(ctx.allowed_projects)
    if not isinstance(repos, Iterable) or isinstance(repos, str):
        raise GitMirrorError("repos must be an iterable of project names")
    return [str(project) for project in repos]


def _render_lines(lines: list[str], *, empty: str) -> str:
    """Render a list of lines as a tool string result."""
    return "\n".join(lines) if lines else empty


def _grep_hit_path(hit: str) -> str | None:
    """Extract the matched file path from a git-grep output line."""
    parts = hit.split(":", 3)
    if len(parts) < _GREP_HIT_MIN_FIELDS:
        return None
    return parts[1] or None


def _is_pickaxe_enabled(value: object) -> bool:
    """Interpret the optional pickaxe argument without truthy-string bugs."""
    return value is True or value == "true"


def _optional_int(value: object) -> int | None:
    """Coerce an optional tool argument to an int, or None if unset."""
    if value is None:
        return None
    if isinstance(value, int | str):
        return int(value)
    msg = f"expected an int-like value, got {type(value).__name__}"
    raise TypeError(msg)


def _append_truncation_notice(text: str, *, truncated: bool, noun: str) -> str:
    """Append a note when a match list was cut off at _MAX_MATCHES_PER_CALL."""
    if not truncated:
        return text
    return (
        f"{text}\n... truncated at {_MAX_MATCHES_PER_CALL} {noun}; refine "
        "your pattern/query or narrow `repos` for more precise results."
    )


async def dispatch_tool_call(
    ctx: ToolContext, *, name: str, arguments: dict[str, object]
) -> str:
    """Execute one tool call and return a plain-string result or error."""
    try:
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            return f"Error: unknown tool {name!r}"
        return await handler(ctx, arguments)
    except KeyError as exc:
        return f"Error: missing required argument {exc}"
    except GitMirrorError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"


async def _handle_read_file(ctx: ToolContext, arguments: dict[str, object]) -> str:
    project = str(arguments["project"])
    path = str(arguments["path"])
    ref = _resolve_ref(ctx, project=project, requested_ref=arguments.get("ref"))
    mirror = mirror_path_for(
        project, mirror_dir=ctx.mirror_dir, allowed_projects=ctx.allowed_projects
    )
    result = await reader.read_file(
        mirror,
        path=path,
        ref=ref,
        start_line=_optional_int(arguments.get("start_line")),
        end_line=_optional_int(arguments.get("end_line")),
    )
    ctx.record_path(project, path)
    return result


async def _handle_grep_repo(ctx: ToolContext, arguments: dict[str, object]) -> str:
    pattern = str(arguments["pattern"])
    ref_arg = arguments.get("ref")
    lines: list[str] = []
    truncated = False
    for project in _resolve_projects(ctx, arguments.get("repos")):
        if truncated:
            break
        ref = _resolve_ref(ctx, project=project, requested_ref=ref_arg)
        mirror = mirror_path_for(
            project,
            mirror_dir=ctx.mirror_dir,
            allowed_projects=ctx.allowed_projects,
        )
        hits = await reader.grep_repo(mirror, pattern=pattern, ref=ref)
        for hit in hits:
            if len(lines) >= _MAX_MATCHES_PER_CALL:
                truncated = True
                break
            path = _grep_hit_path(hit)
            if path:
                ctx.record_path(project, path)
            lines.append(f"{project}: {hit}")
    return _append_truncation_notice(
        _render_lines(lines, empty="(no matches)"),
        truncated=truncated,
        noun="matches",
    )


async def _handle_repo_layout(ctx: ToolContext, arguments: dict[str, object]) -> str:
    project = str(arguments["project"])
    ref = _resolve_ref(ctx, project=project, requested_ref=arguments.get("ref"))
    mirror = mirror_path_for(
        project, mirror_dir=ctx.mirror_dir, allowed_projects=ctx.allowed_projects
    )
    layout = await reader.repo_layout(mirror, ref=ref)
    return _render_lines(
        [f"{directory}\t{count} files" for directory, count in sorted(layout.items())],
        empty="(empty repository)",
    )


async def _handle_git_log_path(ctx: ToolContext, arguments: dict[str, object]) -> str:
    project = str(arguments["project"])
    path = str(arguments["path"])
    ref = _resolve_ref(ctx, project=project, requested_ref=arguments.get("ref"))
    mirror = mirror_path_for(
        project, mirror_dir=ctx.mirror_dir, allowed_projects=ctx.allowed_projects
    )
    commits = await reader.log_path(mirror, path=path, ref=ref)
    ctx.record_path(project, path)
    return _render_lines(commits, empty="(no matching commits)")


async def _handle_git_log_search(ctx: ToolContext, arguments: dict[str, object]) -> str:
    query = str(arguments["query"])
    pickaxe = _is_pickaxe_enabled(arguments.get("pickaxe", False))
    ref_arg = arguments.get("ref")
    lines: list[str] = []
    truncated = False
    for project in _resolve_projects(ctx, arguments.get("repos")):
        if truncated:
            break
        ref = _resolve_ref(ctx, project=project, requested_ref=ref_arg)
        mirror = mirror_path_for(
            project,
            mirror_dir=ctx.mirror_dir,
            allowed_projects=ctx.allowed_projects,
        )
        commits = (
            await reader.log_pickaxe(mirror, query=query, ref=ref)
            if pickaxe
            else await reader.log_search(mirror, query=query, ref=ref)
        )
        for commit in commits:
            if len(lines) >= _MAX_MATCHES_PER_CALL:
                truncated = True
                break
            lines.append(f"{project}: {commit}")
    return _append_truncation_notice(
        _render_lines(lines, empty="(no matching commits)"),
        truncated=truncated,
        noun="commits",
    )


async def _dispatch_http_tool(
    ctx: ToolContext, *, name: str, arguments: dict[str, object]
) -> str:
    """Call a server-side eval HTTP helper tool and return its JSON."""
    if name == "related_issues":
        query = str(arguments.get("query", ""))
        embedding: list[float] | None = None
        if ctx.embed_client is not None and query:
            try:
                embedding = await ctx.embed_client.embed(query, dimensions=1024)
            except Exception as exc:  # noqa: BLE001
                return f"Error computing embedding: {exc}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            if embedding is not None:
                response = await client.post(
                    f"{ctx.eval_server_base_url}/api/eval/related",
                    json={
                        "issue_id": ctx.issue_id,
                        "query": query,
                        "embedding": embedding,
                    },
                    headers={"Authorization": "Bearer " + ctx.eval_api_token},
                )
            else:
                response = await client.get(
                    f"{ctx.eval_server_base_url}/api/eval/related",
                    params={"issue_id": ctx.issue_id, "query": query},
                    headers={"Authorization": "Bearer " + ctx.eval_api_token},
                )
            response.raise_for_status()
            return json.dumps(response.json())
    else:
        path = "/api/eval/issue"
        params = {"ref": str(arguments.get("ref", ""))}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{ctx.eval_server_base_url}{path}",
                params=params,
                headers={"Authorization": "Bearer " + ctx.eval_api_token},
            )
            response.raise_for_status()
            return json.dumps(response.json())


_TOOL_HANDLERS = {
    "read_file": _handle_read_file,
    "grep_repo": _handle_grep_repo,
    "repo_layout": _handle_repo_layout,
    "git_log_path": _handle_git_log_path,
    "git_log_search": _handle_git_log_search,
    "related_issues": lambda ctx, arguments: _dispatch_http_tool(
        ctx, name="related_issues", arguments=arguments
    ),
    "issue_detail": lambda ctx, arguments: _dispatch_http_tool(
        ctx, name="issue_detail", arguments=arguments
    ),
}
