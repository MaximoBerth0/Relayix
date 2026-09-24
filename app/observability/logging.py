import logging
import logging.config

from opentelemetry import trace

from app.infra.config import settings


class TraceContextFilter(logging.Filter):
    """Stamps each record with the active span's ids, so logs join their trace."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "-"
        record.span_id = format(ctx.span_id, "016x") if ctx.is_valid else "-"
        return True


def setup_logging() -> None:
    # verbose in dev, info-level in production
    log_level = logging.INFO if settings.is_production else logging.DEBUG

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "trace_context": {
                "()": "app.observability.logging.TraceContextFilter",
            },
        },
        "formatters": {
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "fmt": "%(asctime)s %(name)s %(levelname)s %(trace_id)s %(span_id)s %(message)s",
                "rename_fields": {
                    "asctime": "timestamp",
                    "levelname": "level",
                    "name": "logger",
                },
            },
            "plain": {
                "format": "%(asctime)s %(name)s %(levelname)s [%(trace_id)s] %(message)s",
            },
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "json" if settings.is_production else "plain",  # structured logs in prod
                "filters": ["trace_context"],
            },
        },
        "root": {
            "level": log_level,
            "handlers": ["stdout"],
        },
        "loggers": {
            "uvicorn": {"propagate": True},
            "uvicorn.access": {"propagate": True},
            "uvicorn.error": {"propagate": True},
            "sqlalchemy.engine": {
                "level": "WARNING",
                "propagate": True,
            },
        },
    }

    logging.config.dictConfig(config)