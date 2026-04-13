"""MoE Libris — Federated Knowledge Exchange Hub.

A lightweight federation server for secure, audited knowledge exchange
between MoE Sovereign instances. Inspired by Fediverse architecture.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, federation
from app.core.config import settings
from app.db.session import close_db, init_db
from app.services.abuse import close_valkey
from app.services.graph import close_driver, init_schema
from app.services.registry import sync_registry

logger = logging.getLogger("libris")

_start_time: float = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and teardown resources."""
    global _start_time
    _start_time = time.time()

    # Initialize database tables
    logger.info("Initializing database...")
    await init_db()

    # Initialize Neo4j schema
    logger.info("Initializing Neo4j schema...")
    try:
        await init_schema()
    except Exception as e:
        logger.warning("Neo4j schema init failed (will retry): %s", e)

    # Initial registry sync (non-blocking)
    logger.info("Starting initial registry sync...")
    asyncio.create_task(_periodic_registry_sync())

    logger.info(
        "MoE Libris started — node_id=%s, url=%s",
        settings.libris_node_id, settings.libris_public_url,
    )

    yield

    # Cleanup
    logger.info("Shutting down...")
    await close_db()
    await close_driver()
    await close_valkey()


async def _periodic_registry_sync():
    """Periodically sync the server registry from GitHub."""
    while True:
        try:
            servers = await asyncio.to_thread(sync_registry)
            logger.info("Registry synced: %d servers", len(servers))
        except Exception as e:
            logger.error("Registry sync failed: %s", e)

        await asyncio.sleep(settings.registry_sync_interval)


app = FastAPI(
    title="MoE Libris",
    description=(
        "Federated Knowledge Exchange Hub for MoE Sovereign instances. "
        "Provides secure, audited knowledge sharing via JSON-LD bundles "
        "with pre-audit pipeline, abuse prevention, and admin review."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS (configurable for MoE Admin UI integration)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(federation.router)
app.include_router(admin.router)


@app.get("/", tags=["health"])
async def root():
    """Health check and instance info."""
    return {
        "service": "moe-libris",
        "version": "1.0.0",
        "node_id": settings.libris_node_id,
        "status": "ok",
        "uptime_seconds": round(time.time() - _start_time, 1),
    }


@app.get("/health", tags=["health"])
async def health():
    """Detailed health check."""
    return {
        "status": "ok",
        "node_id": settings.libris_node_id,
        "uptime_seconds": round(time.time() - _start_time, 1),
    }
