from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1 import chat
from app.core.adapters.registry import build_registry
from app.infra.global_exceptions import AppError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build shared, long-lived resources once per process"""
    app.state.registry = build_registry()
    yield


app = FastAPI(title="Relayix", lifespan=lifespan)

app.include_router(chat.router)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Render any AppError as the normalized JSON error body."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
