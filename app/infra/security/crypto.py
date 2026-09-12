"""Cryptographic helpers for handling secrets (API keys, tokens).
"""

import hashlib
import secrets


def hash_api_key(token: str) -> str:
    """return the hex digest used to look an API key up by its stored hash.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def generate_api_key() -> str:
    """generate a new bearer token to hand a caller. Only the hash is stored,
    so this is the only time the raw value is ever available.
    """
    return f"rlx_{secrets.token_urlsafe(32)}"
