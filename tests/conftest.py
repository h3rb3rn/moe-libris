"""
tests/conftest.py — Shared fixtures and module stubs for MoE Libris tests.

Strategy:
- CRUD / service tests: AsyncMock sessions — no live database required.
  This tests business logic and query construction without a real DB driver.
- API endpoint tests: httpx AsyncClient with FastAPI dependency overrides.
  The real app code is exercised; only the DB session and auth are mocked.
- graph / Neo4j tests: AsyncMock driver — tests commit logic and error handling.
"""

import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEntry, FederationNode


# ── Shared model builders ──────────────────────────────────────────────────────

def make_node(
    node_id: str = "test-node",
    name: str = "Test Node",
    url: str = "https://test.example.com",
    handshake_status: str = "accepted",
    is_blocked: bool = False,
    api_key: str = "moe-sk-testkey123456789",
) -> FederationNode:
    """Create a FederationNode instance for use in tests."""
    import hashlib
    node = FederationNode(
        id=uuid.uuid4().hex,
        node_id=node_id,
        name=name,
        url=url,
        domains=["general"],
        handshake_status=handshake_status,
        is_blocked=is_blocked,
        api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
        api_key_prefix=api_key[:8],
        registered_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        total_pushes=0,
        total_triples_accepted=0,
        total_triples_rejected=0,
    )
    return node


def make_audit_entry(
    node_id: str = "test-node",
    status: str = "pending",
) -> AuditEntry:
    """Create an AuditEntry instance for use in tests."""
    entry = AuditEntry(
        id=uuid.uuid4().hex,
        origin_node_id=node_id,
        status=status,
        bundle_data={"entities": [], "relations": []},
        entity_count=2,
        triple_count=3,
        syntax_ok=True,
        heuristic_ok=True,
        created_at=datetime.now(timezone.utc),
    )
    return entry


# ── Session fixture ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_session() -> AsyncMock:
    """AsyncMock AsyncSession — use in CRUD unit tests.

    Configure return values per test:
        mock_session.execute.return_value.scalar_one_or_none.return_value = node
    """
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.close = AsyncMock()
    return session


# ── Node fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def accepted_node() -> FederationNode:
    """A fully accepted, non-blocked federation node."""
    return make_node()


@pytest.fixture
def blocked_node() -> FederationNode:
    """A blocked federation node."""
    return make_node(node_id="blocked-node", is_blocked=True)


# ── FastAPI test client ────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_client(
    accepted_node: FederationNode,
) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient with overridden get_session and authentication.

    - get_session → yields a fresh AsyncMock session (committed/rolled back safely)
    - get_current_node → returns accepted_node directly (no API key validation)
    Patches app.services.graph.commit_bundle and app.services.abuse to prevent
    live Neo4j / Valkey calls.
    """
    from app.main import app
    from app.db.session import get_session
    from app.core.security import get_current_node

    session_mock = AsyncMock(spec=AsyncSession)
    session_mock.execute = AsyncMock()
    session_mock.add = MagicMock()
    session_mock.commit = AsyncMock()
    session_mock.refresh = AsyncMock()
    session_mock.close = AsyncMock()

    async def _override_session():
        yield session_mock

    def _override_node():
        return accepted_node

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_node] = _override_node

    with (
        patch("app.services.abuse.check_rate_limit", return_value=True),
        patch("app.services.abuse.record_strike", return_value=1),
        patch("app.services.abuse.should_block", return_value=False),
        patch("app.services.graph.commit_bundle", return_value={"entities_created": 1, "relations_created": 1}),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client() -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient authenticated as admin (bypasses node auth, uses admin key)."""
    from app.main import app
    from app.db.session import get_session

    session_mock = AsyncMock(spec=AsyncSession)
    session_mock.execute = AsyncMock()
    session_mock.add = MagicMock()
    session_mock.commit = AsyncMock()
    session_mock.refresh = AsyncMock()
    session_mock.close = AsyncMock()

    async def _override_session():
        yield session_mock

    app.dependency_overrides[get_session] = _override_session

    import os
    admin_key = os.getenv("ADMIN_API_KEY", "test-admin-key-12345678901234")

    with patch.dict("os.environ", {"ADMIN_API_KEY": admin_key}):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Admin-Key": admin_key},
        ) as client:
            yield client

    app.dependency_overrides.clear()
