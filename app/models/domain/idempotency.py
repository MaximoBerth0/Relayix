from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReservationOutcome:
    """Result of trying to claim an idempotency key.

    acquired=True  -> we are the first caller for this key and now own the
                      in-flight slot, run the operation and complete() it.
    acquired=False -> a prior request already claimed the key. `status`,
                      `request_fingerprint` and `response_body` describe it.
    """

    acquired: bool
    status: str | None = None
    request_fingerprint: str | None = None
    response_body: dict[str, Any] | None = None
