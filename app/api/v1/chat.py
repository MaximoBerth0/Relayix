from uuid import UUID

from fastapi import APIRouter, Depends, Header, status

from app.services.gateway_service import GatewayService
from app.services.idempotency_service import IdempotencyService
from app.api.deps import (
    enforce_rate_limit,
    get_current_api_key_id,
    get_gateway_service,
    get_idempotency_service,
)
from app.api.v1.schemas.chat import ChatRequestSchema, ChatResponseSchema

router = APIRouter(
    prefix="/v1/chat",
    tags=["completions"],
)


@router.post(
    "/completions",
    response_model=ChatResponseSchema,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(enforce_rate_limit)],
)
async def create_completion(
    payload: ChatRequestSchema,
    service: GatewayService = Depends(get_gateway_service),
    api_key_id: UUID = Depends(get_current_api_key_id),
    idempotency: IdempotencyService = Depends(get_idempotency_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ChatResponseSchema:
    request = payload.to_domain()

    if idempotency_key is None:
        # no key supplied: preserve the original at-most-effort behaviour.
        result = await service.complete(request, api_key_id)
    else:
        result = await idempotency.execute(
            key=idempotency_key,
            api_key_id=api_key_id,
            request=request,
            operation=lambda: service.complete(request, api_key_id),
        )

    return ChatResponseSchema.from_domain(result)
