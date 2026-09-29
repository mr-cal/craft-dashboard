"""One-time setup performed before the evaluation worker starts polling."""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Any

from craft_dashboard.llm.acp_client import CopilotACPClient
from craft_dashboard.llm.client import LocalLLMClient, OpenRouterClient

if TYPE_CHECKING:
    from craft_dashboard.llm.client import LLMClient


def create_llm_client_for_backend(
    *,
    llm_backend: str = "",
    base_url: str = "",
    api_key: str = "",
    ca_cert: str = "",
    timeout: float | None = None,
    openrouter_api_key: str = "",
    llm_url: str = "",
    llm_api_key: str = "",
) -> LLMClient:
    """Create the completion client for the selected backend."""
    resolved_url = (base_url or llm_url).rstrip("/")
    resolved_api_key = api_key or llm_api_key or openrouter_api_key

    if llm_backend == "copilot-acp":
        return CopilotACPClient(timeout=timeout or 600.0)
    if llm_backend == "openrouter" or "openrouter.ai" in resolved_url.lower():
        return OpenRouterClient(
            base_url=resolved_url if resolved_url else "https://openrouter.ai/api/v1",
            api_key=resolved_api_key,
            timeout=timeout,
        )
    if llm_backend == "local" or resolved_url:
        return LocalLLMClient(
            base_url=resolved_url or "http://localhost:11434/v1",
            api_key=resolved_api_key,
            ca_cert=ca_cert,
            timeout=timeout,
        )
    raise ValueError(f"Unsupported llm backend: {llm_backend}")


def resolve_server_verify(server_ca_cert: str) -> bool | str:
    """Return the ``httpx`` ``verify`` value for the dashboard connection."""
    if not server_ca_cert:
        return True
    expanded_server_ca = pathlib.Path(server_ca_cert).expanduser()
    if not expanded_server_ca.is_file():
        raise FileNotFoundError(
            f"Dashboard CA certificate file not found: '{server_ca_cert}' (resolved to '{expanded_server_ca}'). "
            "Please check your DASHBOARD_CA_CERT configuration."
        )
    return str(expanded_server_ca)


def build_queue_params(
    *,
    project: str,
    open_only: bool,
    force: bool,
    incomplete: bool,
    stale_days: int,
    issue: str,
) -> dict[str, Any]:
    """Build the query string shared by ``/api/eval/next`` and ``/status``."""
    return {
        "project": project,
        "open_only": open_only,
        "force": force,
        "incomplete": incomplete,
        "stale_days": stale_days,
        "external_id": issue,
    }


def describe_filters(*, project: str, open_only: bool) -> str:
    """Return the parenthesised filter summary used in the startup banner."""
    filter_parts = []
    if project:
        filter_parts.append(project)
    if open_only:
        filter_parts.append("open only")
    return f" ({', '.join(filter_parts)})" if filter_parts else ""
