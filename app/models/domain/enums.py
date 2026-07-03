from enum import Enum 

class ProviderEnum(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
