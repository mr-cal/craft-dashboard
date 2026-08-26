"""Application settings loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, loaded from environment variables or .env file."""

    database_url: str = "postgresql+asyncpg://localhost/craft_dashboard"
    github_token: str = ""
    openrouter_api_key: str = ""
    admin_token: str = ""
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    config_file: str = "craft-dashboard.toml"
    log_level: str = "INFO"

    # Eval API token for /api/eval/* endpoints (pull-based evaluation)
    eval_api_token: str = ""

    # Retention window (days) for evaluation transcripts belonging to
    # superseded (non-latest) evaluations. latest=True transcripts are kept
    # indefinitely regardless.
    eval_transcript_retention_days: int = Field(default=30, ge=0)

    # OpenRouter model settings. No default: an unset model must fail loudly
    # rather than silently falling back to some other provider's model (this
    # is exactly how production once ran on Gemini instead of the intended
    # qwen model without anyone noticing).
    openrouter_model: str = ""

    # Legacy local embedding model setting. The continuous `evaluate`
    # worker now always uses OpenRouter embeddings instead.
    local_llm_embedding_model: str = ""

    # Related issues — shown on the issue detail page.
    related_issues_top_n: int = 10
    related_issues_similarity_threshold: float = 0.70

    # Semantic issue search — shown on the issues list page, appended after
    # literal ILIKE matches. Uses the same OpenRouter embedding model as
    # Issue.search_embedding/LLMEvaluation.summary_embedding so query
    # embeddings live in the same vector space.
    #
    # Note: text-embedding-3-small cosine similarities for genuinely related
    # (but non-duplicate) issue text typically land around 0.30-0.45 in
    # practice, not the ~0.70+ one might expect from a "similarity" score.
    # We use a low floor mainly to filter out totally unrelated noise, and
    # rely on cosine-distance ranking + semantic_search_top_n to surface the
    # best matches rather than a high absolute cutoff.
    semantic_search_embedding_model: str = "openai/text-embedding-3-small"
    semantic_search_top_n: int = 10
    semantic_search_similarity_threshold: float = 0.25

    # Database pool settings
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # How many days before re-fetching an issue from GitHub
    refresh_age_days: int = 7

    # Bare git mirror storage for deep-evaluation git tools (Phase 2+).
    # Named with the CRAFT_DASHBOARD_ prefix (unlike this file's other env
    # vars) because it must remain stable across both the VPS mount path
    # and a developer's default cache directory, and is referenced by name
    # in mr-cal/vps-infra's compose files.
    mirror_dir: str = Field(
        default="~/.cache/craft-dashboard/mirrors",
        validation_alias="CRAFT_DASHBOARD_MIRROR_DIR",
    )

    # Max concurrent git subprocesses, independent of eval concurrency. The
    # default of 2 is provisional; Task 7 measures actual peak RSS of a
    # `git grep` across the full mirror set and this default is set from that
    # measurement (design "Open risks / 1 vCPU": Phase 2 measures peak RSS
    # before Phase 6). A dev machine can raise it; the VPS stays at 1-2.
    git_concurrency: int = Field(
        default=2,
        validation_alias="CRAFT_DASHBOARD_GIT_CONCURRENCY",
    )

    # Commit scanner tuning: semantic candidates per commit, minimum cosine
    # similarity to invalidate, and the admin-page warning threshold for one
    # day's total invalidations.
    commit_scanner_top_k: int = 10
    commit_scanner_similarity_threshold: float = 0.70
    commit_scanner_daily_invalidation_warn_threshold: int = 200

    @property
    def model(self) -> str:
        """Return the model for server-side evaluation."""
        return self.openrouter_model

    @property
    def config_path(self) -> Path:
        """Return the validated path to the dashboard config file."""
        config_path = Path(self.config_file)
        if not config_path.exists():
            raise ValueError(f"config_file does not exist: {self.config_file}")
        return config_path

    @property
    def mirror_dir_path(self) -> Path:
        """Return mirror_dir as an expanded, absolute Path.

        `.resolve()` follows `.expanduser()` so a relative override (e.g.
        CRAFT_DASHBOARD_MIRROR_DIR=mirrors during local testing) becomes
        absolute — the git mirror layer and its `git -C <mirror>` calls must
        never depend on the process's current working directory.
        """
        return Path(self.mirror_dir).expanduser().resolve()

    @classmethod
    def validate_config(cls, settings: Settings) -> None:
        """Validate derived configuration requirements for server-side evaluation."""
        if not settings.openrouter_api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required for server-side evaluation"
            )
        if not settings.openrouter_model:
            raise ValueError("OPENROUTER_MODEL is required for server-side evaluation")
        _ = settings.config_path

    def validate_required_secrets(self) -> list[str]:
        """Return a list of warning messages for missing secrets."""
        warnings: list[str] = []
        if not self.admin_token:
            warnings.append(
                "ADMIN_TOKEN is not set. Admin endpoints will reject all requests."
            )
        if not self.github_token:
            warnings.append("GITHUB_TOKEN is not set. Data collection will fail.")
        if not self.eval_api_token:
            warnings.append(
                "EVAL_API_TOKEN is not set. Eval API endpoints will reject all requests."
            )
        return warnings

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}
