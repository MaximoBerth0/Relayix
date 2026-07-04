from app.infra.global_exceptions import AppError


class DomainError(AppError):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_code: str = "DOMAIN_ERROR",
    ):
        super().__init__(message, status_code, error_code)


class InvalidModelName(DomainError):
    def __init__(self, message: str = "Invalid model name"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_MODEL_NAME",
        )

class InvalidToken(DomainError):
    def __init__(self, message: str = "Invalid token"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_TOKEN",
        )


class InvalidTokenPrice(DomainError):
    def __init__(self, message: str = "Invalid token price"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_TOKEN_PRICE",
        )


class InvalidTokenQuantity(DomainError):
    def __init__(self, message: str = "Invalid token quantity"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_TOKEN_QUANTITY",
        )

class InvalidFinishReason(DomainError):
    def __init__(self, message: str = "Invalid finish reason"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_FINISH_REASON",
        )

class InvalidUsageCost(DomainError):
    def __init__(self, message: str = "Invalid usage cost"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_USAGE_COST",
        )

class InvalidApiKeyName(DomainError):
    def __init__(self, message: str = "Invalid API key name"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_API_KEY_NAME",
        )

class InvalidRateLimit(DomainError):
    def __init__(self, message: str = "Invalid rate limit"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_RATE_LIMIT",
        )

class InvalidTokenQuota(DomainError):
    def __init__(self, message: str = "Invalid token quota"):
        super().__init__(
            message=message,
            status_code=400,
            error_code="INVALID_TOKEN_QUOTA",
        )