"""What can go wrong with a model call. All of it ends as an escalation, never a draft.

Messages here are built from status codes and error class names only. They never contain the
prompt, the customer's text, a response body or a key, because they end up in logs and in the
audit trail.
"""


class LLMError(Exception):
    """A model call did not produce a usable answer."""

    @property
    def escalation_reason(self) -> str:
        """The reason recorded when a ticket is escalated because of this error."""
        return f"llm_error:{type(self).__name__}"


class LLMTransientError(LLMError):
    """Worth trying again: the provider was busy, slow or unreachable."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMTimeoutError(LLMTransientError):
    pass


class LLMConnectionError(LLMTransientError):
    pass


class LLMRateLimitError(LLMTransientError):
    pass


class LLMServerError(LLMTransientError):
    pass


class LLMRequestError(LLMError):
    """The provider refused the request (a 4xx other than 429): a bad key, a wrong model name, a
    malformed request. Trying again cannot help."""

    def __init__(self, status_code: int, error_type: str | None = None) -> None:
        detail = f"HTTP {status_code}" + (f" ({error_type})" if error_type else "")
        super().__init__(f"the provider rejected the request: {detail}")
        self.status_code = status_code


class LLMInvalidOutputError(LLMError):
    """The provider answered, but not with output that fits the schema.

    `detail` says which fields were wrong, never what was in them, so it can be put back into a
    retry prompt without carrying customer text.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"the model's output was not usable: {detail}")
        self.detail = detail


class CircuitOpenError(LLMError):
    """Calls are being refused without being made, because the provider has been failing."""


class LLMConfigError(Exception):
    """The LLM settings cannot be turned into a working client. Raised at startup, not per call."""
