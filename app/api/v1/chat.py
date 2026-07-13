from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.services.gateway_service import GatewayService
from app.api.deps import get_current_api_key_id, get_gateway_service
from app.api.v1.schemas.chat import ChatRequestSchema, ChatResponseSchema

router = APIRouter(
    prefix="/v1/chat",
    tags=["completions"],
)


@router.post(
    "/completions",
    response_model=ChatResponseSchema,
    status_code=status.HTTP_200_OK,
)
async def create_completion(
    payload: ChatRequestSchema,
    service: GatewayService = Depends(get_gateway_service),
    api_key_id: UUID = Depends(get_current_api_key_id),
) -> ChatResponseSchema:
    request = payload.to_domain()
    result = await service.complete(request, api_key_id)
    return ChatResponseSchema.from_domain(result)
