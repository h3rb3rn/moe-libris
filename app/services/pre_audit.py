"""Pre-audit pipeline: Syntax → Heuristics → (optional) LLM Triage."""

import re
from dataclasses import dataclass

from app.core.constants import (
    ALLOWED_PREDICATES,
    MAX_BUNDLE_ENTITIES,
    MAX_BUNDLE_TRIPLES,
    MAX_ENTITY_NAME_LEN,
    MAX_TRIPLE_FIELD_LEN,
)
from app.models.schemas import KnowledgeBundle


# Patterns that indicate PII or secrets in triple content
_SENSITIVE_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "email address"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "IP address"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\b"), "JWT token"),
    (re.compile(r"\b(?:sk-|moe-sk-|api-|key-)[A-Za-z0-9]{16,}\b"), "API key"),
    (re.compile(r"\b(?:password|passwd|secret|token)\s*[=:]\s*\S+", re.IGNORECASE), "credential"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "phone number"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "AWS access key"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "private key"),
]

# Constants imported from app.core.constants — do not redeclare here.


@dataclass
class AuditResult:
    """Result of the pre-audit pipeline."""
    syntax_ok: bool
    heuristic_ok: bool
    llm_triage_ok: bool | None  # None = not run
    passed: bool
    notes: list[str]


def stage_1_syntax(bundle: KnowledgeBundle) -> tuple[bool, list[str]]:
    """Stage 1: Validate JSON-LD schema and structural integrity."""
    notes = []

    if not bundle.origin_node_id:
        notes.append("Missing origin_node_id")

    if len(bundle.relations) > MAX_BUNDLE_TRIPLES:
        notes.append(f"Too many triples: {len(bundle.relations)} > {MAX_BUNDLE_TRIPLES}")

    if len(bundle.entities) > MAX_BUNDLE_ENTITIES:
        notes.append(f"Too many entities: {len(bundle.entities)} > {MAX_BUNDLE_ENTITIES}")

    for i, triple in enumerate(bundle.relations):
        if len(triple.subject) > MAX_TRIPLE_FIELD_LEN:
            notes.append(f"Triple {i}: subject too long ({len(triple.subject)} chars)")
        if len(triple.object) > MAX_TRIPLE_FIELD_LEN:
            notes.append(f"Triple {i}: object too long ({len(triple.object)} chars)")
        if not triple.subject or not triple.object or not triple.predicate:
            notes.append(f"Triple {i}: empty subject, predicate, or object")
        if not 0.0 <= triple.confidence <= 1.0:
            notes.append(f"Triple {i}: confidence out of range ({triple.confidence})")

    for i, entity in enumerate(bundle.entities):
        name = entity.get("name", "")
        if not name:
            notes.append(f"Entity {i}: missing name")
        if len(name) > MAX_ENTITY_NAME_LEN:
            notes.append(f"Entity {i}: name too long ({len(name)} chars)")

    return len(notes) == 0, notes


def stage_2_heuristics(bundle: KnowledgeBundle) -> tuple[bool, list[str]]:
    """Stage 2: Scan for PII, secrets, and suspicious content."""
    notes = []

    # Collect all text content from the bundle
    texts = []
    for triple in bundle.relations:
        texts.extend([triple.subject, triple.object, triple.predicate])
    for entity in bundle.entities:
        texts.append(entity.get("name", ""))
        texts.append(entity.get("description", ""))

    # Scan for sensitive patterns
    for text in texts:
        if not text:
            continue
        for pattern, label in _SENSITIVE_PATTERNS:
            if pattern.search(text):
                # Truncate the match for the note
                preview = text[:80] + "..." if len(text) > 80 else text
                notes.append(f"Sensitive content detected ({label}): {preview}")

    # Check predicate whitelist
    for triple in bundle.relations:
        if triple.predicate.upper() not in ALLOWED_PREDICATES:
            notes.append(
                f"Unknown predicate '{triple.predicate}' — "
                f"allowed: {', '.join(sorted(ALLOWED_PREDICATES)[:5])}..."
            )

    return len(notes) == 0, notes


async def run_pre_audit(bundle: KnowledgeBundle) -> AuditResult:
    """Run the full pre-audit pipeline (Stage 1 + 2, optionally Stage 3)."""
    # Stage 1: Syntax
    syntax_ok, syntax_notes = stage_1_syntax(bundle)

    # Stage 2: Heuristics (only if syntax passed)
    if syntax_ok:
        heuristic_ok, heuristic_notes = stage_2_heuristics(bundle)
    else:
        heuristic_ok = False
        heuristic_notes = ["Skipped (syntax validation failed)"]

    all_notes = []
    if syntax_notes:
        all_notes.extend([f"[SYNTAX] {n}" for n in syntax_notes])
    if heuristic_notes and heuristic_notes != ["Skipped (syntax validation failed)"]:
        all_notes.extend([f"[HEURISTIC] {n}" for n in heuristic_notes])

    # Stage 3: LLM Triage (placeholder for v1.1)
    llm_triage_ok = None  # Not implemented in v1

    passed = syntax_ok and heuristic_ok
    return AuditResult(
        syntax_ok=syntax_ok,
        heuristic_ok=heuristic_ok,
        llm_triage_ok=llm_triage_ok,
        passed=passed,
        notes=all_notes,
    )
