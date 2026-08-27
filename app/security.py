"""Password hashing and login tokens, using only the standard library.

No bcrypt, no passlib, no extra dependency -- `hashlib.pbkdf2_hmac` is a real
password KDF and ships with Python. Two properties matter here:

* **Salted.** Every password gets 16 fresh random bytes, so two users with the
  same password get different hashes and one rainbow table cannot crack both.
* **Slow on purpose.** 260,000 SHA-256 rounds costs the user a few
  milliseconds at login and costs an attacker the same per guess.

Login tokens follow the same principle as passwords: the browser holds the
secret, the database holds only its SHA-256 hash. A leaked `auth_tokens` dump
therefore cannot be replayed as a session.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: Bumping this only affects new hashes -- the iteration count is stored inside
#: each hash string, so old passwords keep verifying.
PBKDF2_ROUNDS = 260_000
SALT_BYTES = 16
TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    """Return ``"rounds$salt_hex$hash_hex"`` for storing in ``users``."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"{PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    try:
        rounds_text, salt_hex, expected_hex = stored.split("$")
        rounds = int(rounds_text)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False  # malformed row -- treat as a failed login, never a crash

    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    # compare_digest, not ==, so the comparison cannot be timed to leak the hash.
    return hmac.compare_digest(digest.hex(), expected_hex)


def new_session_token() -> tuple[str, str]:
    """Return ``(token_for_the_cookie, hash_for_the_database)``."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    """SHA-256 of a session token.

    Plain SHA-256 rather than PBKDF2 is correct here: a 32-byte random token
    has no low-entropy guesses to protect against, and this runs on every
    single request.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def validate_password(password: str) -> str | None:
    """Return an error message if the password is unacceptable, else None."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if password.isdigit():
        return "Password cannot be only numbers."
    return None
