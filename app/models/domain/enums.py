from enum import Enum

# used by pricing entity and api_key db model
class ProviderEnum(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"

# used by usage_record entity
VALID_FINISH_REASONS = {"stop", "length", "tool_use", "content_filter"}


class FailoverPolicy(str, Enum):
    """how to treat an *ambiguous* upstream failure, one where we don't know
    whether the provider already executed (and billed) the request.

    AT_MOST_ONCE  - guarantees no double-spend. This is the default.
    AT_LEAST_ONCE - fail over even when the outcome is unknown, accepting the risk
                    of a double execution/charge in exchange for a lower chance of
                    a failed request.
    """
    AT_MOST_ONCE = "at_most_once"
    AT_LEAST_ONCE = "at_least_once"
