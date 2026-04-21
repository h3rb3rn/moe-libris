"""
app/core/constants.py — Single source of truth for shared validation constants.

Any module that validates knowledge bundles (pre_audit, graph, federation API)
must import from here. Never duplicate these values — a divergence between audit
and graph layers would allow predicates that pass audit but silently break commit.
"""

# Allowed Cypher relationship types.
# These are validated at audit time (pre_audit.py) and again at commit time
# (graph.py) to ensure no invalid types reach the Neo4j layer.
# Note: Cypher relationship types cannot be parameterised ($param syntax does not
# work for relationship type labels), so we use string interpolation in graph.py
# — the whitelist check immediately before interpolation is the security boundary.
ALLOWED_PREDICATES: frozenset[str] = frozenset({
    "IS_A", "PART_OF", "TREATS", "CAUSES", "INTERACTS_WITH",
    "CONTRAINDICATES", "DEFINES", "REGULATES", "USES", "IMPLEMENTS",
    "DEPENDS_ON", "EXTENDS", "RELATED_TO", "EQUIVALENT_TO", "AFFECTS",
    "RUNS", "NECESSITATES_PRESENCE", "DEPENDS_ON_LOCATION", "ENABLES_ACTION",
    "HAS_PROPERTY", "BELONGS_TO", "CONTAINS", "PRODUCES", "REQUIRES",
    "SUPPORTS", "CONTRADICTS", "SUPERSEDES",
})

# Field length limits applied uniformly in pre_audit and any future validators.
MAX_ENTITY_NAME_LEN: int = 512
MAX_TRIPLE_FIELD_LEN: int = 512

# Bundle size caps — prevents memory exhaustion from oversized payloads.
MAX_BUNDLE_TRIPLES: int = 5_000
MAX_BUNDLE_ENTITIES: int = 5_000
