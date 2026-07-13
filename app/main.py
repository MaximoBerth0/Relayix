from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1 import chat
from app.core.accounting.pricing import build_pricing_table
from app.core.adapters.registry import build_registry
from app.infra.database.session import AsyncSessionLocal
from app.infra.global_exceptions import AppError
from app.observability.logging import setup_logging
from app.observability.request_id import RequestIdMiddleware
from app.repositories.pricing_repo import load_pricing_rates


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build shared, long-lived resources once per process"""
    setup_logging()  # configure logging before anything emits
    app.state.registry = build_registry()

    # load the pricing rates once and hold the table for the recorder to price against
    async with AsyncSessionLocal() as session:
        rates = await load_pricing_rates(session)
    app.state.pricing = build_pricing_table(rates)
    yield


app = FastAPI(title="Relayix", lifespan=lifespan)

# tag every request with a correlation id (X-Request-ID) for the logs
app.add_middleware(RequestIdMiddleware)

app.include_router(chat.router)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Render any AppError as the normalized JSON error body."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
