"""Password hashing helpers.

Uses PBKDF2-HMAC-SHA256 from the standard library to avoid external native
dependencies. Format: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ITERATIONS = 100_000
_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _ITERATIONS,
    )
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False
