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
    def __init__(self, message: str = "Upstream provider request failed"):
        super().__init__(
            message=message,
            status_code=502,
            error_code="UPSTREAM_ERROR",
        )
