"""Cryptographic helpers for handling secrets (API keys, tokens).
"""

import hashlib


def hash_api_key(token: str) -> str:
    """return the hex digest used to look an API key up by its stored hash.
    """
    return hashlib.sha256(token.encode()).hexdigest()
