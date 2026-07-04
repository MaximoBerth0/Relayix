from enum import Enum 

# used by pricing entity and api_key db model
class ProviderEnum(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"

# used by usage_record entity
VALID_FINISH_REASONS = {"stop", "length", "tool_use", "content_filter"}
