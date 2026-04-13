"""Abuse prevention using Valkey (Redis-compatible) strike counters."""

from valkey.asyncio import Valkey

from app.core.config import settings

_client: Valkey | None = None


async def get_valkey() -> Valkey:
    """Get or create the Valkey connection."""
    global _client
    if _client is None:
        _client = Valkey.from_url(settings.valkey_url, decode_responses=True)
    return _client


async def close_valkey() -> None:
    """Close the Valkey connection."""
    global _client
    if _client:
        await _client.aclose()
        _client = None


def _strike_key(node_id: str, category: str = "general") -> str:
    """Redis key for strike counter."""
    return f"libris:strikes:{category}:{node_id}"


async def record_strike(node_id: str, category: str = "general") -> int:
    """Increment the strike counter for a node. Returns the new count.

    Categories:
      - "syntax": harmless format errors
      - "security": PII/secrets detected (more severe)
      - "general": catch-all
    """
    vk = await get_valkey()
    key = _strike_key(node_id, category)
    count = await vk.incr(key)

    # Set TTL on first strike (sliding window)
    if count == 1:
        await vk.expire(key, settings.strike_window_seconds)

    return count


async def get_strikes(node_id: str, category: str = "general") -> int:
    """Get the current strike count for a node."""
    vk = await get_valkey()
    val = await vk.get(_strike_key(node_id, category))
    return int(val) if val else 0


async def get_all_strikes(node_id: str) -> dict[str, int]:
    """Get all strike categories for a node."""
    return {
        "syntax": await get_strikes(node_id, "syntax"),
        "security": await get_strikes(node_id, "security"),
        "general": await get_strikes(node_id, "general"),
    }


async def should_rate_limit(node_id: str) -> bool:
    """Check if a node should be rate-limited (soft limit exceeded)."""
    total = sum((await get_all_strikes(node_id)).values())
    return total >= settings.strike_soft_limit


async def should_block(node_id: str) -> bool:
    """Check if a node should be blocked (hard limit exceeded)."""
    # Security strikes are weighted 3x
    strikes = await get_all_strikes(node_id)
    weighted = strikes["syntax"] + strikes["general"] + strikes["security"] * 3
    return weighted >= settings.strike_hard_limit


async def clear_strikes(node_id: str) -> None:
    """Clear all strikes for a node (admin action)."""
    vk = await get_valkey()
    for category in ("syntax", "security", "general"):
        await vk.delete(_strike_key(node_id, category))


async def check_rate_limit(node_id: str) -> bool:
    """Check and enforce per-node rate limiting. Returns True if request is allowed."""
    vk = await get_valkey()
    key = f"libris:ratelimit:{node_id}"

    if await should_rate_limit(node_id):
        # Rate-limited nodes: 1 push per hour
        if await vk.exists(key):
            return False
        await vk.setex(key, 3600, "1")
        return True

    # Normal nodes: 60 pushes per hour
    count = await vk.incr(key)
    if count == 1:
        await vk.expire(key, 3600)
    return count <= 60
