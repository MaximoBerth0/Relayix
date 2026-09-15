import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import StreamingResponse

from app.api.deps import (
    enforce_rate_limit,
    get_current_api_key_id,
    get_gateway_service,
    get_idempotency_service,
)
from app.api.v1.schemas.chat import ChatRequestSchema, ChatResponseSchema
from app.infra.global_exceptions import AppError
from app.models.domain.chat import ChatStreamDelta, ChatStreamEvent
from app.services.gateway_service import GatewayService
from app.services.idempotency_service import IdempotencyService

router = APIRouter(
    prefix="/v1/chat",
    tags=["completions"],
)

logger = logging.getLogger(__name__)


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
):
    request = payload.to_domain()

    if payload.stream:
        if idempotency_key is None:
            # no key supplied: preserve the original at-most-effort behaviour.
            events = await service.stream(request, api_key_id)
        else:
            events = await idempotency.execute_stream(
                key=idempotency_key,
                api_key_id=api_key_id,
                request=request,
                operation=lambda: service.stream(request, api_key_id),
            )

        return StreamingResponse(_encode_sse(events), media_type="text/event-stream")

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


async def _encode_sse(events: AsyncIterator[ChatStreamEvent]) -> AsyncIterator[str]:
    """Render normalized stream events as SSE, uniformly across providers.
    """
    try:
        async for event in events:
            if isinstance(event, ChatStreamDelta):
                yield f"event: delta\ndata: {json.dumps({'content': event.content})}\n\n"
            else:
                body = ChatResponseSchema.from_domain(event).model_dump_json()
                yield f"event: done\ndata: {body}\n\n"
    except AppError as exc:
        yield f"event: error\ndata: {json.dumps(exc.to_dict())}\n\n"
    except Exception:
        # the response has already started, so this is the only way left to
        # tell the client anything went wrong instead of just cutting the
        # connection.
        logger.exception("unhandled error mid-stream")
        fallback = {"error_code": "INTERNAL_ERROR", "message": "Internal server error"}
        yield f"event: error\ndata: {json.dumps(fallback)}\n\n"
