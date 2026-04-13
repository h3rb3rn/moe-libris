"""Database CRUD operations."""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEntry, FederationNode, SyncLog


def _hash_key(key: str) -> str:
    """SHA-256 hash of an API key."""
    return hashlib.sha256(key.encode()).hexdigest()


# ─── Federation Nodes ─────────────────────────────────────────────────────────

async def get_node_by_api_key(session: AsyncSession, api_key: str) -> FederationNode | None:
    """Look up a node by its API key hash."""
    key_hash = _hash_key(api_key)
    result = await session.execute(
        select(FederationNode).where(
            FederationNode.api_key_hash == key_hash,
            FederationNode.handshake_status == "accepted",
        )
    )
    return result.scalar_one_or_none()


async def get_node_by_id(session: AsyncSession, node_id: str) -> FederationNode | None:
    """Look up a node by its node_id."""
    result = await session.execute(
        select(FederationNode).where(FederationNode.node_id == node_id)
    )
    return result.scalar_one_or_none()


async def list_nodes(session: AsyncSession) -> list[FederationNode]:
    """List all registered federation nodes."""
    result = await session.execute(
        select(FederationNode).order_by(FederationNode.registered_at.desc())
    )
    return list(result.scalars().all())


async def create_node(
    session: AsyncSession,
    node_id: str,
    name: str,
    url: str,
    domains: list[str],
    handshake_initiated_by: str = "remote",
) -> FederationNode:
    """Register a new federation node."""
    import uuid
    node = FederationNode(
        id=uuid.uuid4().hex,
        node_id=node_id,
        name=name,
        url=str(url),
        domains=domains,
        handshake_initiated_by=handshake_initiated_by,
    )
    session.add(node)
    await session.commit()
    await session.refresh(node)
    return node


async def accept_handshake(
    session: AsyncSession,
    node_id: str,
    api_key: str,
) -> FederationNode | None:
    """Accept a pending handshake and store the API key."""
    node = await get_node_by_id(session, node_id)
    if not node or node.handshake_status != "pending":
        return None

    node.handshake_status = "accepted"
    node.api_key_hash = _hash_key(api_key)
    node.api_key_prefix = api_key[:12]
    await session.commit()
    await session.refresh(node)
    return node


async def block_node(
    session: AsyncSession, node_id: str, reason: str,
) -> None:
    """Block a federation node."""
    await session.execute(
        update(FederationNode)
        .where(FederationNode.node_id == node_id)
        .values(
            is_blocked=True,
            block_reason=reason,
            blocked_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


async def unblock_node(session: AsyncSession, node_id: str) -> None:
    """Unblock a federation node."""
    await session.execute(
        update(FederationNode)
        .where(FederationNode.node_id == node_id)
        .values(is_blocked=False, block_reason=None, blocked_at=None)
    )
    await session.commit()


async def update_node_last_seen(session: AsyncSession, node_id: str) -> None:
    """Update the last_seen_at timestamp for a node."""
    await session.execute(
        update(FederationNode)
        .where(FederationNode.node_id == node_id)
        .values(last_seen_at=datetime.now(timezone.utc))
    )
    await session.commit()


async def increment_node_push_stats(
    session: AsyncSession, node_id: str, accepted: int, rejected: int,
) -> None:
    """Update push statistics for a node."""
    await session.execute(
        update(FederationNode)
        .where(FederationNode.node_id == node_id)
        .values(
            total_pushes=FederationNode.total_pushes + 1,
            total_triples_accepted=FederationNode.total_triples_accepted + accepted,
            total_triples_rejected=FederationNode.total_triples_rejected + rejected,
            last_push_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


# ─── Audit Queue ──────────────────────────────────────────────────────────────

async def create_audit_entry(
    session: AsyncSession,
    origin_node_id: str,
    bundle_data: dict,
    triple_count: int,
    entity_count: int,
    syntax_ok: bool,
    heuristic_ok: bool,
    llm_triage_ok: bool | None = None,
    pre_audit_notes: str | None = None,
) -> AuditEntry:
    """Insert a new entry into the audit queue."""
    entry = AuditEntry(
        origin_node_id=origin_node_id,
        bundle_data=bundle_data,
        triple_count=triple_count,
        entity_count=entity_count,
        syntax_ok=syntax_ok,
        heuristic_ok=heuristic_ok,
        llm_triage_ok=llm_triage_ok,
        pre_audit_notes=pre_audit_notes,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def list_audit_entries(
    session: AsyncSession,
    status: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[AuditEntry], int]:
    """List audit queue entries with pagination."""
    query = select(AuditEntry).order_by(AuditEntry.created_at.desc())
    count_query = select(func.count(AuditEntry.id))

    if status:
        query = query.where(AuditEntry.status == status)
        count_query = count_query.where(AuditEntry.status == status)

    total = (await session.execute(count_query)).scalar() or 0
    result = await session.execute(
        query.offset((page - 1) * per_page).limit(per_page)
    )
    return list(result.scalars().all()), total


async def get_audit_entry(session: AsyncSession, entry_id: str) -> AuditEntry | None:
    """Get a single audit entry by ID."""
    result = await session.execute(
        select(AuditEntry).where(AuditEntry.id == entry_id)
    )
    return result.scalar_one_or_none()


async def approve_audit_entry(
    session: AsyncSession, entry_id: str, reviewed_by: str,
) -> AuditEntry | None:
    """Approve an audit entry and mark it for graph commit."""
    entry = await get_audit_entry(session, entry_id)
    if not entry or entry.status != "pending":
        return None

    now = datetime.now(timezone.utc)
    entry.status = "approved"
    entry.reviewed_at = now
    entry.reviewed_by = reviewed_by
    entry.committed_to_graph_at = now
    await session.commit()
    await session.refresh(entry)
    return entry


async def reject_audit_entry(
    session: AsyncSession, entry_id: str, reviewed_by: str, reason: str,
) -> AuditEntry | None:
    """Reject an audit entry."""
    entry = await get_audit_entry(session, entry_id)
    if not entry or entry.status != "pending":
        return None

    entry.status = "rejected"
    entry.reviewed_at = datetime.now(timezone.utc)
    entry.reviewed_by = reviewed_by
    entry.rejection_reason = reason
    await session.commit()
    await session.refresh(entry)
    return entry


# ─── Sync Log ─────────────────────────────────────────────────────────────────

async def log_sync(
    session: AsyncSession,
    node_id: str,
    direction: str,
    triple_count: int,
    entity_count: int,
    status: str,
    detail: str | None = None,
) -> SyncLog:
    """Record a sync operation in the log."""
    entry = SyncLog(
        node_id=node_id,
        direction=direction,
        triple_count=triple_count,
        entity_count=entity_count,
        status=status,
        detail=detail,
    )
    session.add(entry)
    await session.commit()
    return entry


# ─── Statistics ───────────────────────────────────────────────────────────────

async def get_stats(session: AsyncSession) -> dict:
    """Get aggregate statistics."""
    nodes_total = (await session.execute(
        select(func.count(FederationNode.id))
    )).scalar() or 0

    nodes_active = (await session.execute(
        select(func.count(FederationNode.id)).where(
            FederationNode.handshake_status == "accepted",
            FederationNode.is_blocked == False,  # noqa: E712
        )
    )).scalar() or 0

    nodes_blocked = (await session.execute(
        select(func.count(FederationNode.id)).where(FederationNode.is_blocked == True)  # noqa: E712
    )).scalar() or 0

    pending_audits = (await session.execute(
        select(func.count(AuditEntry.id)).where(AuditEntry.status == "pending")
    )).scalar() or 0

    return {
        "total_nodes": nodes_total,
        "active_nodes": nodes_active,
        "blocked_nodes": nodes_blocked,
        "pending_audits": pending_audits,
    }
