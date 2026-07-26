"""Password verification helpers for the public Streamlit deployment shell."""

from __future__ import annotations

import base64
import hashlib
import hmac


def password_digest(password: str, salt_b64: str, iterations: int) -> str:
    """Return a base64 PBKDF2-HMAC-SHA256 password digest."""

    salt = base64.b64decode(salt_b64, validate=True)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return base64.b64encode(digest).decode("ascii")


def verify_password(
    password: str,
    salt_b64: str,
    expected_digest: str,
    iterations: int = 600_000,
) -> bool:
    """Compare a submitted password with the configured digest."""

    if not password or iterations < 100_000:
        return False
    try:
        actual = password_digest(password, salt_b64, iterations)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected_digest)
