# MoE Libris — Development Guide

## Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for running tests outside the container)
- A running PostgreSQL instance (provided via `docker compose`)
- A running Neo4j instance (provided via `docker compose`)
- A running Valkey instance (provided via `docker compose`)

## Local Setup

```bash
# 1. Clone and enter the repository
git clone https://github.com/h3rb3rn/moe-libris
cd moe-libris

# 2. Configure environment
cp .env.example .env
# Edit .env — minimum required:
#   POSTGRES_PASSWORD, NEO4J_PASSWORD, LIBRIS_NODE_ID, LIBRIS_ADMIN_KEY

# 3. Start all services
docker compose up -d

# API:      http://localhost:8080
# OpenAPI:  http://localhost:8080/docs
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | ✓ | PostgreSQL password for the `libris` role |
| `NEO4J_PASSWORD` | ✓ | Neo4j password (local federation graph) |
| `REDIS_PASSWORD` | ✓ | Valkey/Redis password (abuse rate-limiting) |
| `LIBRIS_NODE_ID` | ✓ | Unique ID for this Libris instance (used in handshakes) |
| `LIBRIS_PUBLIC_URL` | ✓ | Public HTTPS URL where other nodes reach this instance |
| `LIBRIS_ADMIN_KEY` | ✓ | Admin API key — **minimum 32 chars**, random string |
| `REGISTRY_REPO_URL` | — | Git URL of the moe-libris-registry (server discovery) |
| `REGISTRY_SYNC_INTERVAL` | — | Registry sync frequency in seconds (default: 3600) |
| `LOG_LEVEL` | — | `debug` / `info` / `warning` (default: `info`) |

> **Security:** `LIBRIS_ADMIN_KEY` must be at least 32 characters. The server
> refuses to start if the key is missing or too short (`assert_secrets_configured()`
> is called during lifespan startup).

## Running Tests

```bash
# Inside the container (recommended — matches production Python/deps)
docker exec moe-libris-api python3 -m pytest tests/ -v

# On the host (requires pytest, pytest-asyncio, httpx installed)
pip install pytest pytest-asyncio httpx
python3 -m pytest tests/ -v
```

Current test suite: **51 tests** across 5 modules.

| Module | Coverage |
|---|---|
| `tests/test_api_federation.py` | `/v1/federation/*` endpoints — push, pull, handshake, auth |
| `tests/test_pre_audit.py` | Pre-audit pipeline — syntax, heuristics, PII detection |
| `tests/test_security.py` | Cypher injection, timing-safe auth, input validation |
| `tests/test_abuse.py` | Strike system, rate limiting, auto-block thresholds |

## Architecture

```
app/
├── api/
│   ├── federation.py   # /v1/federation/* — push, pull, handshake, verify
│   └── admin.py        # /v1/admin/* — audit queue, node management, stats
├── core/
│   ├── config.py       # Settings (pydantic-settings, reads from .env)
│   ├── constants.py    # ALLOWED_PREDICATES, MAX_* limits (single source of truth)
│   ├── exceptions.py   # LibrisError hierarchy (AuditError, GraphError, ...)
│   └── security.py     # Admin key validation, node API key lookup
├── db/
│   ├── models.py       # SQLAlchemy ORM: FederationNode, AuditEntry, SyncLog
│   ├── crud.py         # All DB operations (async)
│   └── session.py      # Async SQLAlchemy engine + session factory
├── models/
│   └── schemas.py      # Pydantic request/response schemas + KnowledgeBundle
└── services/
    ├── pre_audit.py    # 3-stage pipeline: syntax → heuristics → (optional LLM)
    ├── graph.py        # Neo4j driver: commit_bundle(), pull_since()
    ├── abuse.py        # Valkey-backed strike + rate-limit system
    └── registry.py     # moe-libris-registry Git sync + cache
```

## Pre-Audit Pipeline

Knowledge bundles submitted via `POST /v1/federation/push` pass through three stages
before entering the audit queue:

1. **Syntax** — Pydantic schema validation (field lengths, confidence range, predicate whitelist)
2. **Heuristics** — Regex scans for PII (email, phone, IP), secrets (API keys, JWT tokens, private keys)
3. **LLM triage** *(optional)* — Can be enabled per-deployment for semantic quality checks

Failed bundles are rejected, a strike is recorded against the node, and after
`ABUSE_BLOCK_THRESHOLD` strikes the node is automatically blocked.

## Authentication

**Node auth** (`X-API-Key` header): API keys are stored as SHA-256 hashes in PostgreSQL.
The prefix (first 8 chars) is stored in plaintext for log correlation.

**Admin auth** (`X-Admin-Key` header): Compared against `LIBRIS_ADMIN_KEY` via
`hmac.compare_digest()` (timing-safe). Empty-key bypass is explicitly blocked.

## Adding a New Endpoint

1. Add route to `app/api/federation.py` or `app/api/admin.py`
2. Add request/response schema to `app/models/schemas.py` if needed
3. Add CRUD operation to `app/db/crud.py` if DB access is required
4. Write integration test in `tests/test_api_federation.py` or `tests/test_api_admin.py`
5. Rebuild: `docker compose build && docker compose up -d`
