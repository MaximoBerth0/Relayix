"""service-layer errors, rendered by the global AppError handler"""

from app.infra.global_exceptions import AppError


class IdempotencyKeyConflict(AppError):
    """The same Idempotency-Key was reused for a different request body"""

    def __init__(
        self,
        message: str = "Idempotency-Key was already used for a different request",
    ):
        super().__init__(
            message=message,
            status_code=422,
            error_code="IDEMPOTENCY_KEY_CONFLICT",
        )


class IdempotencyInProgress(AppError):
    """The original request for this key is still running"""

    def __init__(
        self,
        message: str = "A request with this Idempotency-Key is still being processed",
    ):
        super().__init__(
            message=message,
            status_code=409,
            error_code="IDEMPOTENCY_IN_PROGRESS",
        )


class IdempotencyOutcomeUnknown(AppError):
    """A previous attempt for this key ended with an unknown upstream outcome."""

    def __init__(self, detail: str | None = None):
        message = (
            "A previous request with this Idempotency-Key may already have been "
            "executed by the provider, so it will not be retried automatically. "
            "Use a new Idempotency-Key to issue a new request."
        )
        if detail:
            message = f"{message} Original failure: {detail}"
        super().__init__(
            message=message,
            status_code=409,
            error_code="IDEMPOTENCY_OUTCOME_UNKNOWN",
        )


class IdempotencyStoreUnavailable(AppError):
    """The idempotency backend (Redis) could not be reached"""

    def __init__(
        self,
        message: str = "Idempotency store is unavailable, retry the request",
    ):
        super().__init__(
            message=message,
            status_code=503,
            error_code="IDEMPOTENCY_STORE_UNAVAILABLE",
        )
