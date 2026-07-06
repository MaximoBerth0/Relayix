from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.adapters.registry import AdapterRegistry, build_registry
from app.infra.global_exceptions import AppError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build shared, long-lived resources once per process"""
    app.state.registry = build_registry()
    yield


def get_registry(request: Request) -> AdapterRegistry:
    """FastAPI dependency: resolve the process-wide adapter registry."""
    return request.app.state.registry


app = FastAPI(title="Relayix", lifespan=lifespan)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Render any AppError as the normalized JSON error body."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
