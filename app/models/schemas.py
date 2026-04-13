"""Pydantic v2 schemas for API request/response models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


# ─── Enums ────────────────────────────────────────────────────────────────────

class AuditStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class HandshakeStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class KnowledgeDomain(str, Enum):
    GENERAL = "general"
    CODE_REVIEWER = "code_reviewer"
    TECHNICAL_SUPPORT = "technical_support"
    CREATIVE_WRITER = "creative_writer"
    MATH = "math"
    SCIENCE = "science"
    LEGAL_ADVISOR = "legal_advisor"
    MEDICAL_CONSULT = "medical_consult"
    REASONING = "reasoning"
    DATA_ANALYST = "data_analyst"
    TRANSLATION = "translation"


# ─── Knowledge Bundle (JSON-LD) ──────────────────────────────────────────────

class Triple(BaseModel):
    """A single knowledge triple (subject → predicate → object)."""
    subject: str = Field(..., max_length=512)
    subject_type: str = Field(..., max_length=128)
    predicate: str = Field(..., max_length=128)
    object: str = Field(..., max_length=512)
    object_type: str = Field(..., max_length=128)
    confidence: float = Field(..., ge=0.0, le=1.0)
    domain: str = Field(..., max_length=64)


class KnowledgeBundle(BaseModel):
    """JSON-LD knowledge bundle for federation push/pull."""
    context: str = Field(
        alias="@context",
        default="https://moe-sovereign.org/knowledge/v1",
    )
    origin_node_id: str = Field(..., max_length=64)
    pushed_at: datetime
    entities: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)
    relations: list[Triple] = Field(default_factory=list, max_length=5000)
    syntheses: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)

    model_config = {"populate_by_name": True}


# ─── Federation Push/Pull ─────────────────────────────────────────────────────

class PushRequest(BaseModel):
    """Incoming push from a federation node."""
    bundle: KnowledgeBundle


class PushResponse(BaseModel):
    """Response to a push request."""
    accepted: int = 0
    rejected: int = 0
    queued: int = 0
    detail: str = ""


class PullRequest(BaseModel):
    """Query parameters for pulling delta updates."""
    last_sync: datetime | None = None
    domains: list[KnowledgeDomain] | None = None
    limit: int = Field(default=1000, le=10000)


class PullResponse(BaseModel):
    """Response with approved knowledge since last_sync."""
    bundle: KnowledgeBundle
    total: int
    has_more: bool


# ─── Handshake (Node Pairing) ────────────────────────────────────────────────

class HandshakeRequest(BaseModel):
    """Initial handshake from a remote Libris server."""
    node_id: str = Field(..., max_length=64)
    url: HttpUrl
    name: str = Field(..., max_length=128)
    domains: list[KnowledgeDomain]


class HandshakeAccept(BaseModel):
    """Admin accepts a handshake — returns the API key for the remote node."""
    api_key: str


class HandshakeConfirm(BaseModel):
    """Remote node confirms handshake by sharing its API key."""
    api_key_for_you: str


# ─── Audit Queue ──────────────────────────────────────────────────────────────

class AuditEntry(BaseModel):
    """An entry in the audit queue."""
    id: str
    origin_node_id: str
    status: AuditStatus
    triple_count: int
    entity_count: int
    preview: list[Triple] = Field(default_factory=list)
    rejection_reason: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None


class AuditDecision(BaseModel):
    """Admin decision on a queued entry."""
    reason: str = Field(default="", max_length=500)


class AuditQueueResponse(BaseModel):
    """Paginated audit queue listing."""
    entries: list[AuditEntry]
    total: int
    page: int
    per_page: int


# ─── Node Management ─────────────────────────────────────────────────────────

class NodeInfo(BaseModel):
    """Information about a federation node."""
    node_id: str
    name: str
    url: str
    domains: list[str]
    status: str  # active, rate_limited, blocked
    handshake_status: HandshakeStatus
    version: str | None = None
    last_seen_at: datetime | None = None
    strikes: int
    total_pushes: int
    total_triples_accepted: int
    last_push_at: datetime | None = None
    last_pull_at: datetime | None = None
    registered_at: datetime


class NodeListResponse(BaseModel):
    """List of registered federation nodes."""
    nodes: list[NodeInfo]
    total: int


# ─── Registry (Server Discovery) ─────────────────────────────────────────────

class RegistryServer(BaseModel):
    """A server entry from the moe-libris-registry."""
    id: str
    name: str
    url: str
    contact: str
    description: str = ""
    domains: list[str]
    public: bool = True
    verified: bool = False
    added: str  # ISO date


class RegistryListResponse(BaseModel):
    """Available servers from the registry."""
    servers: list[RegistryServer]
    last_synced: datetime | None = None


# ─── Stats ────────────────────────────────────────────────────────────────────

class VersionCount(BaseModel):
    """Version distribution entry."""
    version: str
    count: int


class LibrisStats(BaseModel):
    """Global Libris server statistics."""
    node_id: str
    total_nodes: int
    active_nodes: int
    blocked_nodes: int
    pending_nodes: int
    pending_audits: int
    approved_triples: int
    approved_entities: int
    total_pushes: int
    total_pulls: int
    version_distribution: list[VersionCount] = []
    recently_active_nodes: int = 0  # nodes seen in last 24h
    uptime_seconds: float
