"""Authentication helpers for admin endpoints."""

import secrets

from fastapi import HTTPException, Request, status


def get_admin_bearer_token(request: Request, authorization: str = "") -> str:
    """Return the bearer token from the header or admin session cookie."""
    if authorization:
        return authorization

    cookie_token = request.cookies.get("admin_session")
    if cookie_token:
        return f"Bearer {cookie_token}"

    return authorization


def verify_admin_token(token: str, admin_token: str) -> None:
    """Verify that the provided bearer token matches the admin token.

    Args:
        token: The Authorization header value (e.g., 'Bearer <token>').
        admin_token: The expected admin token from settings.

    Raises:
        HTTPException: If the token is invalid or missing.

    """
    if not admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication is not configured.",
        )

    if not token.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Use 'Bearer <token>'.",
        )

    provided = token.removeprefix("Bearer ")
    if not secrets.compare_digest(provided, admin_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token.",
        )
