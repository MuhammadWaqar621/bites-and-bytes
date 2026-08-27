"""Cookie-based authentication.

The browser holds a random 32-byte token in an **HttpOnly** cookie; the
database holds only its SHA-256 hash. HttpOnly matters: JavaScript on the page
cannot read the cookie, so a cross-site scripting bug cannot steal the session.

:func:`current_user` is the dependency every private route depends on. Because
it raises 401 rather than returning None, forgetting it on a new route is a
loud failure, not a silent data leak.
"""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session as DbSession

from app import repository as repo
from app.config import get_settings
from app.db import get_db
from app.models import User

COOKIE_NAME = "bb_session"


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the login cookie.

    ``secure`` is deliberately False: this app is served over plain HTTP on
    localhost, and a Secure cookie would simply never be sent. Set it to True
    the moment this runs behind HTTPS.
    """
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def current_user(
    bb_session: str | None = Cookie(default=None),
    db: DbSession = Depends(get_db),
) -> User:
    """Resolve the signed-in user, or reject the request with 401."""
    user = repo.user_for_token(db, bb_session or "")
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Please sign in to continue.",
        )
    return user
