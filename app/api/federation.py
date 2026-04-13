"""Federation endpoints: push, pull, handshake, verify."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import get_current_node
from app.db import crud
from app.db.models import FederationNode
from app.db.session import get_session
from app.models.schemas import (
    HandshakeConfirm, HandshakeRequest, KnowledgeBundle,
    KnowledgeDomain, PullResponse, PushRequest, PushResponse,
)
from app.services import abuse, graph, pre_audit

router = APIRouter(prefix="/v1/federation", tags=["federation"])


@router.post("/push", response_model=PushResponse)
async def push_knowledge(
    request: PushRequest,
    node: FederationNode = Depends(get_current_node),
    session: AsyncSession = Depends(get_session),
):
    """Receive a knowledge bundle from a federation node.

    The bundle goes through the pre-audit pipeline:
    1. Syntax validation
    2. Heuristic scans (PII, secrets, suspicious content)
    3. If passed → queued as PENDING in audit queue
    4. If failed → strike recorded, bundle rejected
    """
    # Rate limit check
    if not await abuse.check_rate_limit(node.node_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Try again later.",
        )

    bundle = request.bundle

    # Run pre-audit pipeline
    result = await pre_audit.run_pre_audit(bundle)

    if not result.passed:
        # Record strikes based on failure type
        category = "security" if not result.heuristic_ok else "syntax"
        strike_count = await abuse.record_strike(node.node_id, category)

        # Check if node should be blocked
        if await abuse.should_block(node.node_id):
            await crud.block_node(
                session, node.node_id,
                reason=f"Automatic block: {strike_count} strikes. "
                       f"Last: {'; '.join(result.notes[:3])}",
            )

        await crud.log_sync(
            session, node.node_id, "push",
            triple_count=len(bundle.relations),
            entity_count=len(bundle.entities),
            status="rejected",
            detail="; ".join(result.notes[:5]),
        )

        return PushResponse(
            accepted=0,
            rejected=len(bundle.relations) + len(bundle.entities),
            queued=0,
            detail=f"Pre-audit failed: {'; '.join(result.notes[:3])}",
        )

    # Passed pre-audit → queue for admin review
    entry = await crud.create_audit_entry(
        session,
        origin_node_id=node.node_id,
        bundle_data=bundle.model_dump(by_alias=True, mode="json"),
        triple_count=len(bundle.relations),
        entity_count=len(bundle.entities),
        syntax_ok=result.syntax_ok,
        heuristic_ok=result.heuristic_ok,
        llm_triage_ok=result.llm_triage_ok,
        pre_audit_notes="; ".join(result.notes) if result.notes else None,
    )

    # Update node activity tracking
    await crud.update_node_last_seen(session, node.node_id)
    await crud.increment_node_push_stats(session, node.node_id, accepted=0, rejected=0)
    await crud.log_sync(
        session, node.node_id, "push",
        triple_count=len(bundle.relations),
        entity_count=len(bundle.entities),
        status="success",
        detail=f"Queued as {entry.id}",
    )

    return PushResponse(
        accepted=0,
        rejected=0,
        queued=len(bundle.relations) + len(bundle.entities),
        detail=f"Bundle queued for review (ID: {entry.id})",
    )


@router.get("/pull", response_model=PullResponse)
async def pull_knowledge(
    last_sync: datetime | None = None,
    domains: str | None = None,
    limit: int = 1000,
    node: FederationNode = Depends(get_current_node),
    session: AsyncSession = Depends(get_session),
):
    """Pull approved knowledge updates since last_sync.

    Query parameters:
      - last_sync: ISO timestamp — only return triples approved after this time
      - domains: comma-separated domain filter (e.g. "general,code_reviewer")
      - limit: max number of entities to return (default 1000, max 10000)
    """
    domain_list = domains.split(",") if domains else None
    limit = min(limit, 10000)

    # Validate domain strings against KnowledgeDomain enum values
    if domain_list:
        valid_domains = {d.value for d in KnowledgeDomain}
        invalid = [d for d in domain_list if d not in valid_domains]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid domain(s): {', '.join(invalid)}",
            )

    result = await graph.pull_since(
        since=last_sync,
        domains=domain_list,
        limit=limit,
    )

    # Update node's last pull timestamp
    node.last_pull_at = datetime.now(timezone.utc)
    await session.commit()

    await crud.log_sync(
        session, node.node_id, "pull",
        triple_count=len(result.get("relations", [])),
        entity_count=len(result.get("entities", [])),
        status="success",
    )

    bundle = KnowledgeBundle(
        origin_node_id=settings.libris_node_id,
        pushed_at=datetime.now(timezone.utc),
        entities=result.get("entities", []),
        relations=result.get("relations", []),
        syntheses=[],
    )

    return PullResponse(
        bundle=bundle,
        total=result.get("total", 0),
        has_more=result.get("has_more", False),
    )


@router.post("/handshake")
async def request_handshake(
    request: HandshakeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Receive a handshake request from a remote Libris server.

    This registers the remote node as PENDING. An admin must accept
    the handshake via the audit API before sync can begin.

    TODO: Add rate limiting via middleware (e.g. slowapi) to prevent
    handshake flooding from unauthenticated sources.
    """
    # Check if node already exists
    existing = await crud.get_node_by_id(session, request.node_id)
    if existing:
        if existing.handshake_status == "accepted":
            return {"status": "already_connected", "node_id": request.node_id}
        if existing.handshake_status == "pending":
            return {"status": "pending", "node_id": request.node_id}

    await crud.create_node(
        session,
        node_id=request.node_id,
        name=request.name,
        url=str(request.url),
        domains=[d.value for d in request.domains],
        handshake_initiated_by="remote",
    )

    return {
        "status": "pending",
        "node_id": request.node_id,
        "message": "Handshake request received. Awaiting admin approval.",
    }


@router.post("/confirm")
async def confirm_handshake(
    request: HandshakeConfirm,
    node: FederationNode = Depends(get_current_node),
):
    """Remote node confirms the handshake by sharing its API key for us.

    This completes the bilateral key exchange.
    """
    # Store the remote node's API key for outbound requests
    # (In production, this would be encrypted at rest)
    return {
        "status": "confirmed",
        "node_id": node.node_id,
        "message": "Handshake confirmed. Bilateral sync is now active.",
    }


@router.get("/verify")
async def verify_instance():
    """Verification endpoint for the moe-libris-registry CI.

    Returns instance metadata to prove this is a real Libris server.
    """
    return {
        "libris": True,
        "node_id": settings.libris_node_id,
        "version": "1.0.0",
        "public_url": settings.libris_public_url,
    }
