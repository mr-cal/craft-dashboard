"""Tests for the LLM exception hierarchy."""

from craft_dashboard.llm.exceptions import (
    LLMError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMValidationError,
)


class TestLLMExceptions:
    """Tests for LLM domain exceptions."""

    def test_quota_error_uses_llm_base_class(self) -> None:
        """Quota errors inherit from the shared LLM exception base."""
        error = LLMQuotaError("quota")

        assert isinstance(error, LLMQuotaError)
        assert isinstance(error, LLMError)
        assert isinstance(error, Exception)

    def test_other_domain_errors_use_shared_base_class(self) -> None:
        """Timeout, validation, and rate-limit errors all share the base type."""
        assert isinstance(LLMTimeoutError("timeout"), LLMError)
        assert isinstance(LLMValidationError("invalid"), LLMError)
        assert isinstance(LLMRateLimitError("slow down"), LLMError)
