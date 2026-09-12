from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.deps import get_api_key_service, get_current_admin
from app.api.v1.schemas.api_key import (
    ApiKeyCreateRequestSchema,
    ApiKeyCreateResponseSchema,
    ApiKeySchema,
)
from app.services.api_key_service import ApiKeyService

router = APIRouter(
    prefix="/v1/api-keys",
    tags=["api-keys"],
    dependencies=[Depends(get_current_admin)],
)


@router.post(
    "",
    response_model=ApiKeyCreateResponseSchema,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    payload: ApiKeyCreateRequestSchema,
    service: ApiKeyService = Depends(get_api_key_service),
) -> ApiKeyCreateResponseSchema:
    """Create a new api key. The raw token is returned once and never again."""
    api_key, token = await service.create_api_key(
        payload.name,
        rate_limit_rpm=payload.rate_limit_rpm,
        monthly_token_quota=payload.monthly_token_quota,
    )
    return ApiKeyCreateResponseSchema.from_domain(api_key, token)


@router.get(
    "",
    response_model=list[ApiKeySchema],
    status_code=status.HTTP_200_OK,
)
async def list_api_keys(
    service: ApiKeyService = Depends(get_api_key_service),
) -> list[ApiKeySchema]:
    """List every api key, most recently created first."""
    api_keys = await service.list_api_keys()
    return [ApiKeySchema.from_domain(api_key) for api_key in api_keys]


@router.post(
    "/{api_key_id}/revoke",
    response_model=ApiKeySchema,
    status_code=status.HTTP_200_OK,
)
async def revoke_api_key(
    api_key_id: UUID,
    service: ApiKeyService = Depends(get_api_key_service),
) -> ApiKeySchema:
    """Deactivate an api key. It stays visible in list_api_keys as inactive."""
    api_key = await service.revoke_api_key(api_key_id)
    return ApiKeySchema.from_domain(api_key)
