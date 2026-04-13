"""Admin endpoints: audit queue, node management, stats, registry."""

import asyncio
import logging
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("libris.admin")

from app.core.security import require_admin
from app.db import crud
from app.db.session import get_session
from app.models.schemas import (
    AuditDecision, AuditEntry, AuditQueueResponse,
    NodeInfo, NodeListResponse, LibrisStats, RegistryListResponse,
    VersionCount,
)
from app.services import abuse, graph, registry
from app.core.config import settings

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ─── Audit Queue ──────────────────────────────────────────────────────────────

@router.get("/audit/queue", response_model=AuditQueueResponse)
async def list_audit_queue(
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """List entries in the audit queue with optional status filter."""
    entries, total = await crud.list_audit_entries(
        session, status=status_filter, page=page, per_page=per_page,
    )

    return AuditQueueResponse(
        entries=[
            AuditEntry(
                id=e.id,
                origin_node_id=e.origin_node_id,
                status=e.status,
                triple_count=e.triple_count,
                entity_count=e.entity_count,
                preview=[],  # Could extract first N triples from bundle_data
                rejection_reason=e.rejection_reason,
                created_at=e.created_at,
                reviewed_at=e.reviewed_at,
                reviewed_by=e.reviewed_by,
            )
            for e in entries
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/audit/{entry_id}")
async def get_audit_entry(
    entry_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get full details of an audit entry including the bundle data."""
    entry = await crud.get_audit_entry(session, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audit entry not found")

    return {
        "id": entry.id,
        "origin_node_id": entry.origin_node_id,
        "status": entry.status,
        "triple_count": entry.triple_count,
        "entity_count": entry.entity_count,
        "bundle_data": entry.bundle_data,
        "syntax_ok": entry.syntax_ok,
        "heuristic_ok": entry.heuristic_ok,
        "llm_triage_ok": entry.llm_triage_ok,
        "pre_audit_notes": entry.pre_audit_notes,
        "rejection_reason": entry.rejection_reason,
        "created_at": entry.created_at,
        "reviewed_at": entry.reviewed_at,
        "reviewed_by": entry.reviewed_by,
        "committed_to_graph_at": entry.committed_to_graph_at,
    }


@router.post("/audit/{entry_id}/approve")
async def approve_entry(
    entry_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Approve an audit entry and commit its data to the global Neo4j graph."""
    entry = await crud.get_audit_entry(session, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audit entry not found")
    if entry.status != "pending":
        raise HTTPException(status_code=400, detail=f"Entry is already {entry.status}")

    # Commit to Neo4j first — only mark as approved if this succeeds
    try:
        commit_stats = await graph.commit_bundle(
            entry.bundle_data, entry.origin_node_id,
        )
    except Exception as e:
        logger.error("Neo4j commit failed for audit entry %s: %s", entry_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to commit to graph: {e}",
        )

    # Mark as approved in database (only reached if Neo4j commit succeeded)
    await crud.approve_audit_entry(session, entry_id, reviewed_by="admin")

    # Update node stats
    await crud.increment_node_push_stats(
        session, entry.origin_node_id,
        accepted=commit_stats["entities_created"] + commit_stats["relations_created"],
        rejected=0,
    )

    return {
        "status": "approved",
        "committed": commit_stats,
    }


@router.post("/audit/{entry_id}/reject")
async def reject_entry(
    entry_id: str,
    decision: AuditDecision,
    session: AsyncSession = Depends(get_session),
):
    """Reject an audit entry with a reason."""
    entry = await crud.reject_audit_entry(
        session, entry_id, reviewed_by="admin", reason=decision.reason,
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found or not pending")

    return {"status": "rejected", "reason": decision.reason}


# ─── Node Management ──────────────────────────────────────────────────────────

@router.get("/nodes", response_model=NodeListResponse)
async def list_nodes(session: AsyncSession = Depends(get_session)):
    """List all registered federation nodes."""
    nodes = await crud.list_nodes(session)

    node_infos = []
    for n in nodes:
        strikes = await abuse.get_all_strikes(n.node_id)
        total_strikes = sum(strikes.values())

        node_status = "active"
        if n.is_blocked:
            node_status = "blocked"
        elif await abuse.should_rate_limit(n.node_id):
            node_status = "rate_limited"

        node_infos.append(NodeInfo(
            node_id=n.node_id,
            name=n.name,
            url=n.url,
            domains=n.domains or [],
            status=node_status,
            handshake_status=n.handshake_status,
            version=n.version,
            last_seen_at=n.last_seen_at,
            strikes=total_strikes,
            total_pushes=n.total_pushes,
            total_triples_accepted=n.total_triples_accepted,
            last_push_at=n.last_push_at,
            last_pull_at=n.last_pull_at,
            registered_at=n.registered_at,
        ))

    return NodeListResponse(nodes=node_infos, total=len(node_infos))


@router.post("/nodes/{node_id}/accept")
async def accept_node_handshake(
    node_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Accept a pending handshake and generate an API key for the node."""
    node = await crud.get_node_by_id(session, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.handshake_status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Node handshake is already {node.handshake_status}",
        )

    # Generate API key for the remote node
    api_key = f"lbk-{secrets.token_hex(24)}"
    await crud.accept_handshake(session, node_id, api_key)

    # Fetch version from remote node's verify endpoint
    try:
        async with httpx.AsyncClient(timeout=5.0) as hc:
            verify_resp = await hc.get(f"{node.url.rstrip('/')}/v1/federation/verify")
            if verify_resp.status_code == 200:
                verify_data = verify_resp.json()
                node.version = verify_data.get("version")
                node.last_seen_at = datetime.now(timezone.utc)
                await session.commit()
    except Exception:
        pass  # Version fetch is best-effort

    return {
        "status": "accepted",
        "api_key": api_key,
        "version": node.version,
        "message": "Share this API key with the remote node operator.",
    }


@router.post("/nodes/{node_id}/reject")
async def reject_node_handshake(
    node_id: str,
    decision: AuditDecision,
    session: AsyncSession = Depends(get_session),
):
    """Reject a pending handshake."""
    node = await crud.get_node_by_id(session, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    node.handshake_status = "rejected"
    await session.commit()

    return {"status": "rejected", "reason": decision.reason}


@router.post("/nodes/{node_id}/block")
async def block_node(
    node_id: str,
    decision: AuditDecision,
    session: AsyncSession = Depends(get_session),
):
    """Manually block a federation node."""
    await crud.block_node(session, node_id, reason=decision.reason or "Manual admin block")
    return {"status": "blocked", "node_id": node_id}


@router.post("/nodes/{node_id}/unblock")
async def unblock_node(
    node_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Unblock a federation node and clear its strikes."""
    await crud.unblock_node(session, node_id)
    await abuse.clear_strikes(node_id)
    return {"status": "unblocked", "node_id": node_id}


# ─── Registry (Server Discovery) ─────────────────────────────────────────────

@router.get("/registry", response_model=RegistryListResponse)
async def list_registry_servers():
    """List available servers from the moe-libris-registry."""
    servers, last_synced = registry.get_cached_servers()
    return RegistryListResponse(servers=servers, last_synced=last_synced)


@router.post("/registry/sync")
async def sync_registry_now():
    """Force an immediate sync of the registry from GitHub."""
    servers = await asyncio.to_thread(registry.sync_registry)
    return {
        "synced": True,
        "server_count": len(servers),
        "servers": [s.id for s in servers],
    }


# ─── Statistics ───────────────────────────────────────────────────────────────

@router.get("/stats", response_model=LibrisStats)
async def get_stats(session: AsyncSession = Depends(get_session)):
    """Get Libris server statistics with version distribution."""
    db_stats = await crud.get_stats(session)
    graph_stats = await graph.get_graph_stats()

    # Version distribution and activity tracking
    nodes = await crud.list_nodes(session)
    version_counts = Counter(
        n.version or "unknown" for n in nodes
        if n.handshake_status == "accepted"
    )
    version_dist = [
        VersionCount(version=v, count=c)
        for v, c in sorted(version_counts.items())
    ]

    # Nodes active in last 24h
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recently_active = sum(
        1 for n in nodes
        if n.last_seen_at and n.last_seen_at > cutoff
    )

    # Pending handshakes
    pending_nodes = sum(1 for n in nodes if n.handshake_status == "pending")

    return LibrisStats(
        node_id=settings.libris_node_id,
        total_nodes=db_stats["total_nodes"],
        active_nodes=db_stats["active_nodes"],
        blocked_nodes=db_stats["blocked_nodes"],
        pending_nodes=pending_nodes,
        pending_audits=db_stats["pending_audits"],
        approved_triples=graph_stats["approved_triples"],
        approved_entities=graph_stats["approved_entities"],
        total_pushes=0,
        total_pulls=0,
        version_distribution=version_dist,
        recently_active_nodes=recently_active,
        uptime_seconds=0,
    )
