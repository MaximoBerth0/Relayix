from app.infra.global_exceptions import AppError


class CoreError(AppError):
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: str = "CORE_ERROR",
    ):
        super().__init__(message, status_code, error_code)


class PricingRateNotFound(CoreError):
    def __init__(self, message: str = "No pricing rate in effect"):
        super().__init__(
            message=message,
            status_code=422,
            error_code="PRICING_RATE_NOT_FOUND",
        )


class CounterNotRegistered(CoreError):
    def __init__(self, message: str = "No counter registered for provider"):
        super().__init__(
            message=message,
            status_code=500,
            error_code="COUNTER_NOT_REGISTERED",
        )


class AdapterNotRegistered(CoreError):
    def __init__(self, message: str = "No adapter registered for provider"):
        super().__init__(
            message=message,
            status_code=500,
            error_code="ADAPTER_NOT_REGISTERED",
        )


class UpstreamError(CoreError):
    """Base for any upstream provider failure.

      - UpstreamUnavailable: the request provably never executed.
      - UpstreamAmbiguous:   the request may have executed (double-spend risk).
    """

    def __init__(
        self,
        message: str = "Upstream provider request failed",
        error_code: str = "UPSTREAM_ERROR",
    ):
        super().__init__(
            message=message,
            status_code=502,
            error_code=error_code,
        )


class UpstreamUnavailable(UpstreamError):
    """The provider provably did NOT execute the request — the call never left
    us, or it was rejected before reaching the model (connection refused, DNS,
    TLS, a pre-execution 4xx, or an open circuit). Always safe to fail over."""

    def __init__(self, message: str = "Upstream provider unavailable"):
        super().__init__(message=message, error_code="UPSTREAM_UNAVAILABLE")


class UpstreamAmbiguous(UpstreamError):
    """The request may have executed and billed, Failing over to a
    different provider risks a double-spend, so it is gated on FailoverPolicy."""

    def __init__(self, message: str = "Upstream provider outcome unknown"):
        super().__init__(message=message, error_code="UPSTREAM_AMBIGUOUS")


class CircuitOpen(UpstreamUnavailable):
    def __init__(self, message: str = "Provider circuit is open"):
        CoreError.__init__(
            self,
            message=message,
            status_code=503,
            error_code="CIRCUIT_OPEN",
        )


class RateLimitExceeded(CoreError):
    """raised when a caller has spent its allowance for the current window"""

    def __init__(self, retry_after_s: float, message: str = "Rate limit exceeded"):
        self.retry_after_s = retry_after_s
        super().__init__(
            message=message,
            status_code=429,
            error_code="RATE_LIMITED",
        )

    def to_dict(self) -> dict[str, object]:
        body = super().to_dict()
        body["retry_after_s"] = round(self.retry_after_s, 3)
        return body


class RateLimiterUnavailable(CoreError):
    """Internal signal, a limiter backend could not be reached.
    """

    def __init__(self, message: str = "Rate limiter backend unavailable"):
        super().__init__(
            message=message,
            status_code=503,
            error_code="RATE_LIMITER_UNAVAILABLE",
        )
