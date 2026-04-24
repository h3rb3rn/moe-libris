"""
tests/test_api_federation.py — Integration tests for /v1/federation/* endpoints.

Uses httpx AsyncClient with mocked DB session and authentication (see conftest.py).
These tests verify: HTTP routing, request validation, pre-audit integration,
strike logic, and response schemas — without a live database.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_audit_entry, make_node


# ── Helper: minimal valid KnowledgeBundle payload ──────────────────────────────

def _push_payload(
    entities=None,
    relations=None,
    origin="test-node",
):
    return {
        "bundle": {
            "origin_node_id": origin,
            "pushed_at": "2026-04-22T10:00:00Z",
            "entities": entities or [{"name": "Python", "type": "Language"}],
            "relations": [
                {
                    "subject": "Python", "subject_type": "Language",
                    "predicate": "IS_A",
                    "object": "Programming Language", "object_type": "Concept",
                    "confidence": 0.95, "domain": "general",
                }
            ] if relations is None else relations,
        }
    }


# ── POST /v1/federation/push ───────────────────────────────────────────────────

@pytest.mark.asyncio
class TestFederationPush:
    async def test_valid_bundle_queued(self, test_client):
        """A clean bundle passes pre-audit and gets queued (202-style response)."""
        with patch("app.db.crud.create_audit_entry", new_callable=AsyncMock) as mock_create, \
             patch("app.db.crud.update_node_last_seen", new_callable=AsyncMock), \
             patch("app.db.crud.increment_node_push_stats", new_callable=AsyncMock), \
             patch("app.db.crud.log_sync", new_callable=AsyncMock):
            mock_create.return_value = make_audit_entry()
            resp = await test_client.post(
                "/v1/federation/push",
                json=_push_payload(),
                headers={"X-API-Key": "moe-sk-testkey123456789"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["queued"] > 0
        assert "queued for review" in body["detail"].lower()

    async def test_pii_bundle_rejected(self, test_client):
        """A bundle with PII fails pre-audit and returns a rejection response."""
        pii_relations = [
            {
                "subject": "User", "subject_type": "Person",
                "predicate": "IS_A",
                "object": "user@example.com",  # email → PII
                "object_type": "Email",
                "confidence": 0.9, "domain": "general",
            }
        ]
        with patch("app.db.crud.log_sync", new_callable=AsyncMock), \
             patch("app.db.crud.block_node", new_callable=AsyncMock):
            resp = await test_client.post(
                "/v1/federation/push",
                json=_push_payload(relations=pii_relations),
                headers={"X-API-Key": "moe-sk-testkey123456789"},
            )
        # Pre-audit should flag PII and return rejection
        body = resp.json()
        assert body["rejected"] > 0 or "failed" in body.get("detail", "").lower()

    async def test_missing_api_key_returns_401(self, test_client):
        """Requests without X-API-Key header are rejected with 401."""
        # Disable the node override so real auth is exercised
        from app.main import app
        from app.core.security import get_current_node
        app.dependency_overrides.pop(get_current_node, None)

        resp = await test_client.post("/v1/federation/push", json=_push_payload())
        assert resp.status_code == 401

        # Restore override for other tests
        from tests.conftest import make_node as _make_node
        app.dependency_overrides[get_current_node] = lambda: _make_node()

    async def test_rate_limit_returns_429(self, test_client):
        """When rate limit is exceeded, push returns 429."""
        with patch("app.services.abuse.check_rate_limit", return_value=False):
            resp = await test_client.post(
                "/v1/federation/push",
                json=_push_payload(),
                headers={"X-API-Key": "moe-sk-testkey123456789"},
            )
        assert resp.status_code == 429

    async def test_invalid_predicate_rejected(self, test_client):
        """A bundle with an unknown predicate fails pre-audit."""
        bad_relations = [
            {
                "subject": "A", "subject_type": "Thing",
                "predicate": "UNKNOWN_BAD_PREDICATE",
                "object": "B", "object_type": "Thing",
                "confidence": 0.9, "domain": "general",
            }
        ]
        with patch("app.db.crud.log_sync", new_callable=AsyncMock), \
             patch("app.db.crud.block_node", new_callable=AsyncMock):
            resp = await test_client.post(
                "/v1/federation/push",
                json=_push_payload(relations=bad_relations),
                headers={"X-API-Key": "moe-sk-testkey123456789"},
            )
        body = resp.json()
        assert body["rejected"] > 0


# ── GET /v1/federation/pull ────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestFederationPull:
    async def test_pull_returns_200(self, test_client):
        """Pull without filters returns a successful response."""
        with patch("app.services.graph.pull_since", new_callable=AsyncMock) as mock_pull, \
             patch("app.db.crud.update_node_last_seen", new_callable=AsyncMock), \
             patch("app.db.crud.log_sync", new_callable=AsyncMock):
            mock_pull.return_value = {"entities": [], "relations": [], "total": 0}
            resp = await test_client.get(
                "/v1/federation/pull",
                headers={"X-API-Key": "moe-sk-testkey123456789"},
            )
        assert resp.status_code == 200

    async def test_pull_invalid_domain_returns_400(self, test_client):
        """Pull with an unknown domain filter returns 400."""
        resp = await test_client.get(
            "/v1/federation/pull?domains=nonexistent_domain",
            headers={"X-API-Key": "moe-sk-testkey123456789"},
        )
        assert resp.status_code == 400


# ── POST /v1/federation/handshake ─────────────────────────────────────────────

@pytest.mark.asyncio
class TestFederationHandshake:
    async def test_new_node_handshake_queued(self, test_client):
        """A new node handshake is accepted and queued as pending."""
        with patch("app.db.crud.get_node_by_id", new_callable=AsyncMock, return_value=None), \
             patch("app.db.crud.create_node", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = make_node(handshake_status="pending")
            resp = await test_client.post(
                "/v1/federation/handshake",
                json={
                    "node_id": "new-peer-node",
                    "name": "Peer Node",
                    "url": "https://peer.example.com",
                    "domains": ["general"],
                    "version": "1.0.0",
                },
                headers={"X-API-Key": "moe-sk-testkey123456789"},
            )
        assert resp.status_code in (200, 201, 202)
