# MoE Libris

**Federated Knowledge Exchange Hub for MoE Sovereign instances.**

MoE Libris is a lightweight federation server that enables secure, audited knowledge sharing between [MoE Sovereign](https://github.com/moe-sovereign/moe-infra) instances. Inspired by Fediverse architecture (ActivityPub/Friendica), it provides a hub-and-spoke model for exchanging knowledge graph triples via JSON-LD bundles.

## Architecture

```
MoE Sovereign A ──push──▶ MoE Libris ◀──push── MoE Sovereign B
                 ◀──pull──           ──pull──▶
```

**Core Components:**
- **Federation API** — Push/Pull JSON-LD knowledge bundles between instances
- **Pre-Audit Pipeline** — Syntax validation, heuristic PII/secret scanning
- **Audit Queue** — Admin review before knowledge enters the global graph
- **Abuse Prevention** — Graduated strike system with rate limiting (Valkey)
- **Global Knowledge Graph** — Approved triples stored in Neo4j
- **Server Discovery** — Decentralized via [moe-libris-registry](https://github.com/moe-sovereign/moe-libris-registry)

## Quick Start

```bash
cp .env.example .env
# Edit .env with your passwords and node identity
docker compose up -d
```

The API is available at `http://localhost:8080`. Interactive docs at `/docs`.

## API Overview

### Federation (Node-to-Node)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/federation/push` | POST | Push knowledge bundle (requires API key) |
| `/v1/federation/pull` | GET | Pull approved knowledge since timestamp |
| `/v1/federation/handshake` | POST | Initiate node pairing |
| `/v1/federation/verify` | GET | Registry verification endpoint |

### Admin (Management)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/admin/audit/queue` | GET | List audit queue entries |
| `/v1/admin/audit/{id}/approve` | POST | Approve and commit to graph |
| `/v1/admin/audit/{id}/reject` | POST | Reject with reason |
| `/v1/admin/nodes` | GET | List federation nodes |
| `/v1/admin/nodes/{id}/accept` | POST | Accept handshake |
| `/v1/admin/nodes/{id}/block` | POST | Block a node |
| `/v1/admin/registry` | GET | List discovered servers |
| `/v1/admin/stats` | GET | Server statistics |

## Stack

- **FastAPI** — Async Python web framework
- **PostgreSQL** — Audit queue, node registry, sync log
- **Neo4j** — Global approved knowledge graph
- **Valkey** — Rate limiting, strike counters

## License

MIT
