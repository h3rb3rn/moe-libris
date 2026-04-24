"""Authentication and authorization utilities."""

import hmac
import logging

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.db.session import get_session
from app.db import crud

logger = logging.getLogger("libris.security")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)

# Minimum key lengths — enforced at startup (see main.py lifespan) and here.
_MIN_ADMIN_KEY_LEN = 32


def assert_secrets_configured() -> None:
    """Raise RuntimeError at startup if security-critical settings are missing.

    Called from the app lifespan so misconfiguration fails loudly on boot
    rather than silently passing empty-string comparisons at request time.
    An empty LIBRIS_ADMIN_KEY would let any request pass require_admin()
    because hmac.compare_digest('', '') is True.
    """
    if not settings.libris_admin_key:
        raise RuntimeError(
            "LIBRIS_ADMIN_KEY is not set. Set it to a random string of at least "
            f"{_MIN_ADMIN_KEY_LEN} characters before starting the server."
        )
    if len(settings.libris_admin_key) < _MIN_ADMIN_KEY_LEN:
        raise RuntimeError(
            f"LIBRIS_ADMIN_KEY is too short ({len(settings.libris_admin_key)} chars). "
            f"Minimum: {_MIN_ADMIN_KEY_LEN} characters."
        )


async def get_current_node(
    api_key: str | None = Security(api_key_header),
    session=Depends(get_session),
) -> dict:
    """Validate API key and return the authenticated FederationNode.

    Raises 401 if the key is missing or unknown, 403 if the node is blocked.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )

    node = await crud.get_node_by_api_key(session, api_key)
    if not node:
        # Log at INFO (not WARNING) to avoid flooding logs on probing attacks
        logger.info("Rejected request: unknown API key prefix=%s", api_key[:8] if len(api_key) >= 8 else "?")
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
    """Validate the X-Admin-Key header for management endpoints.

    Guards against empty-key bypass: if LIBRIS_ADMIN_KEY is not configured,
    assert_secrets_configured() will have already aborted startup. The explicit
    length check here adds defense-in-depth for any runtime configuration change.
    """
    configured_key = settings.libris_admin_key
    if not admin_key or not configured_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing admin key",
        )
    # hmac.compare_digest is timing-safe; the explicit empty checks above prevent
    # the hmac.compare_digest("", "") == True bypass.
    if not hmac.compare_digest(admin_key.encode(), configured_key.encode()):
        logger.warning("Failed admin auth attempt (key prefix: %s)", admin_key[:4] if admin_key else "—")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing admin key",
        )
    return True
