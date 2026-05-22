"""Authentication helpers for admin endpoints."""

import secrets

from fastapi import HTTPException, status


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
