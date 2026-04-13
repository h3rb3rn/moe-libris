"""Authentication and authorization utilities."""

import hmac

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.db.session import get_session
from app.db import crud

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def get_current_node(
    api_key: str | None = Security(api_key_header),
    session=Depends(get_session),
) -> dict:
    """Validate API key and return the federation node context.

    Returns dict with node_id, permissions, and rate limit status.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )

    node = await crud.get_node_by_api_key(session, api_key)
    if not node:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    if node.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Node is blocked due to abuse policy violation",
        )

    return node


async def require_admin(
    admin_key: str | None = Security(admin_key_header),
) -> bool:
    """Validate the admin key for management endpoints."""
    if not admin_key or not hmac.compare_digest(admin_key, settings.libris_admin_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing admin key",
        )
    return True
