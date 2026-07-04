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

