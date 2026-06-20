"""Domain exceptions for LLM integrations."""


class LLMError(Exception):
    """Base class for all LLM-related errors."""


class LLMQuotaError(LLMError):
    """Raised when the configured LLM provider quota is exhausted."""


class LLMTimeoutError(LLMError):
    """Raised when an LLM request times out."""


class LLMValidationError(LLMError):
    """Raised when an LLM response fails validation."""


class LLMRateLimitError(LLMError):
    """Raised when an LLM provider returns a rate-limit response."""


class LLMUnavailableError(LLMError):
    """Raised when the LLM backend is unreachable or returns a server error."""
