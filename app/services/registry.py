"""Registry sync service — pulls server list from moe-libris-registry Git repo."""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.models.schemas import RegistryServer

logger = logging.getLogger(__name__)

CACHE_DIR = Path("/app/registry-cache")
SERVERS_DIR = CACHE_DIR / "servers"


def sync_registry() -> list[RegistryServer]:
    """Clone or pull the registry repo and parse server entries.

    This runs git commands synchronously (called via asyncio.to_thread).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    repo_url = settings.registry_repo_url
    if not repo_url:
        logger.warning("No registry repo URL configured, skipping sync")
        return []

    try:
        if (CACHE_DIR / ".git").exists():
            # Pull latest
            subprocess.run(
                ["git", "-C", str(CACHE_DIR), "pull", "--ff-only", "-q"],
                capture_output=True, text=True, timeout=30, check=True,
            )
        else:
            # Fresh clone
            subprocess.run(
                ["git", "clone", "--depth=1", repo_url, str(CACHE_DIR)],
                capture_output=True, text=True, timeout=60, check=True,
            )
    except subprocess.CalledProcessError as e:
        logger.error("Registry git sync failed: %s", e.stderr)
        # Fall through — use whatever is cached

    return _parse_servers()


def _parse_servers() -> list[RegistryServer]:
    """Parse all server JSON files from the cached registry."""
    servers = []

    if not SERVERS_DIR.exists():
        return servers

    for path in sorted(SERVERS_DIR.glob("*.json")):
        if path.name == "example.json":
            continue  # Skip the template
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            servers.append(RegistryServer(**data))
        except Exception as e:
            logger.warning("Failed to parse %s: %s", path.name, e)

    return servers


def get_cached_servers() -> tuple[list[RegistryServer], datetime | None]:
    """Get servers from the local cache without syncing.

    Returns (servers, last_modified_time).
    """
    git_dir = CACHE_DIR / ".git"
    last_sync = None

    if git_dir.exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(CACHE_DIR), "log", "-1", "--format=%cI"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                last_sync = datetime.fromisoformat(result.stdout.strip())
        except Exception:
            pass

    return _parse_servers(), last_sync
