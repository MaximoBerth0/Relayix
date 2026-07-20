from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_api_key_id, get_usage_service
from app.api.v1.schemas.usage import UsageRecordSchema, UsageSummarySchema
from app.services.usage_service import UsageService

router = APIRouter(
    prefix="/v1/usage",
    tags=["usage"],
)

""" things i need to complete
- read methods in repo 
- get_usage_repo dependency
- the schemas
- register the router 
"""

@router.get(
    "",
    response_model=UsageSummarySchema,
    status_code=status.HTTP_200_OK,
)
async def get_usage_summary(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    service: UsageService = Depends(get_usage_service),
    api_key_id: UUID = Depends(get_current_api_key_id),
) -> UsageSummarySchema:
    """Aggregate token and cost totals for the caller's api key."""
    summary = await service.usage_summary(api_key_id, since=since, until=until)
    return UsageSummarySchema.from_domain(summary)


@router.get(
    "/records",
    response_model=list[UsageRecordSchema],
    status_code=status.HTTP_200_OK,
)
async def list_usage_records(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: UsageService = Depends(get_usage_service),
    api_key_id: UUID = Depends(get_current_api_key_id),
) -> list[UsageRecordSchema]:
    """Return the caller's usage records, most recent first."""
    records = await service.list_usage_records(
        api_key_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [UsageRecordSchema.from_domain(record) for record in records]
