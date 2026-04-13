"""SQLAlchemy ORM models for the Nexus database."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, Integer, String, Text,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


# ─── Federation Nodes ─────────────────────────────────────────────────────────

class FederationNode(Base):
    """A registered federation peer (another Nexus or MoE Sovereign instance)."""

    __tablename__ = "federation_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(512))
    domains: Mapped[list] = mapped_column(ARRAY(String(64)), default=list)

    # Authentication
    api_key_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_key_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Handshake state
    handshake_status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending, accepted, rejected
    handshake_initiated_by: Mapped[str] = mapped_column(
        String(16), default="remote"
    )  # remote, local

    # Abuse prevention
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Statistics
    total_pushes: Mapped[int] = mapped_column(Integer, default=0)
    total_triples_accepted: Mapped[int] = mapped_column(Integer, default=0)
    total_triples_rejected: Mapped[int] = mapped_column(Integer, default=0)
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    audit_entries: Mapped[list["AuditEntry"]] = relationship(
        back_populates="origin_node", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_nodes_handshake", "handshake_status"),
        Index("idx_nodes_blocked", "is_blocked"),
    )


# ─── Audit Queue ──────────────────────────────────────────────────────────────

class AuditEntry(Base):
    """A queued knowledge bundle pending admin review."""

    __tablename__ = "audit_queue"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    origin_node_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("federation_nodes.node_id", ondelete="CASCADE"),
        index=True,
    )

    # Bundle content (stored as JSONB for flexible querying)
    bundle_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    triple_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)

    # Pre-audit results
    syntax_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    heuristic_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_triage_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pre_audit_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Review state
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending, approved, rejected
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance
    committed_to_graph_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    # Relationships
    origin_node: Mapped["FederationNode"] = relationship(back_populates="audit_entries")

    __table_args__ = (
        Index("idx_audit_status_created", "status", "created_at"),
    )


# ─── Sync Log ─────────────────────────────────────────────────────────────────

class SyncLog(Base):
    """Log of push/pull operations for auditing."""

    __tablename__ = "sync_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(4))  # push, pull
    triple_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))  # success, rejected, error
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True,
    )
