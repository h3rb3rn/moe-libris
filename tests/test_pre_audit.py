"""Tests for the pre-audit pipeline — PII detection, secret scanning, validation."""

import pytest
from app.models.schemas import KnowledgeBundle, Triple
from app.services.pre_audit import (
    stage_1_syntax, stage_2_heuristics, run_pre_audit,
    ALLOWED_PREDICATES, MAX_BUNDLE_TRIPLES, MAX_BUNDLE_ENTITIES,
)


def _bundle(entities=None, relations=None, origin="test-node"):
    """Helper to create a KnowledgeBundle for testing."""
    return KnowledgeBundle(
        origin_node_id=origin,
        pushed_at="2026-04-13T00:00:00Z",
        entities=entities or [],
        relations=relations or [],
    )


def _triple(subject="Python", predicate="IS_A", obj="Language",
            confidence=0.9, domain="general"):
    return Triple(
        subject=subject, subject_type="Concept",
        predicate=predicate,
        object=obj, object_type="Concept",
        confidence=confidence, domain=domain,
    )


# ─── Stage 1: Syntax Validation ──────────────────────────────────────────────

class TestSyntaxValidation:
    def test_valid_bundle_passes(self):
        bundle = _bundle(
            entities=[{"name": "Python", "type": "Language"}],
            relations=[_triple()],
        )
        ok, notes = stage_1_syntax(bundle)
        assert ok
        assert notes == []

    def test_empty_origin_fails(self):
        bundle = _bundle(origin="")
        ok, notes = stage_1_syntax(bundle)
        assert not ok
        assert any("origin_node_id" in n for n in notes)

    def test_too_many_triples_fails(self):
        # Pydantic enforces MAX_BUNDLE_TRIPLES on KnowledgeBundle — constructing
        # a bundle with >5000 items raises ValidationError before stage_1_syntax
        # ever sees it. The schema and pre_audit both enforce the same limit.
        import pydantic
        relations = [_triple(subject=f"E{i}") for i in range(MAX_BUNDLE_TRIPLES + 1)]
        with pytest.raises(pydantic.ValidationError, match="too_long"):
            _bundle(relations=relations)

    def test_too_many_entities_fails(self):
        import pydantic
        entities = [{"name": f"Entity{i}"} for i in range(MAX_BUNDLE_ENTITIES + 1)]
        with pytest.raises(pydantic.ValidationError, match="too_long"):
            _bundle(entities=entities)

    def test_empty_subject_fails(self):
        bundle = _bundle(relations=[_triple(subject="")])
        ok, notes = stage_1_syntax(bundle)
        assert not ok

    def test_confidence_out_of_range(self):
        """Pydantic rejects confidence > 1.0 at model level."""
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            _triple(confidence=1.5)

    def test_entity_missing_name(self):
        bundle = _bundle(entities=[{"type": "Concept"}])
        ok, notes = stage_1_syntax(bundle)
        assert not ok
        assert any("missing name" in n.lower() for n in notes)

    def test_subject_too_long(self):
        """Pydantic rejects subject > 512 chars at model level."""
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            _triple(subject="A" * 600)


# ─── Stage 2: Heuristic PII/Secret Scanning ─────────────────────────────────

class TestHeuristicScanning:
    def test_clean_bundle_passes(self):
        bundle = _bundle(
            entities=[{"name": "Python"}],
            relations=[_triple()],
        )
        ok, notes = stage_2_heuristics(bundle)
        assert ok

    def test_email_detected(self):
        bundle = _bundle(entities=[{"name": "admin@company.com"}])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok
        assert any("email" in n.lower() for n in notes)

    def test_ip_address_detected(self):
        bundle = _bundle(relations=[_triple(subject="Server 192.168.1.100")])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok
        assert any("IP" in n or "ip" in n.lower() for n in notes)

    def test_jwt_token_detected(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
        bundle = _bundle(entities=[{"name": f"Token: {jwt}"}])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok
        assert any("JWT" in n or "jwt" in n.lower() for n in notes)

    def test_api_key_detected(self):
        bundle = _bundle(entities=[{"name": "key: sk-abc123456789012345678901234567890123"}])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok

    def test_moe_api_key_detected(self):
        # Fake key pattern for testing detection — not a real credential
        fake_key = "moe-sk-" + "0" * 48
        bundle = _bundle(entities=[{"name": fake_key}])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok

    def test_password_in_text_detected(self):
        bundle = _bundle(entities=[{"name": "password=supersecret123"}])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok
        assert any("credential" in n.lower() for n in notes)

    def test_private_key_detected(self):
        bundle = _bundle(entities=[{"name": "-----BEGIN RSA PRIVATE KEY-----"}])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok

    def test_aws_key_detected(self):
        bundle = _bundle(entities=[{"name": "AKIAIOSFODNN7EXAMPLE"}])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok

    def test_phone_number_detected(self):
        bundle = _bundle(entities=[{"name": "Call 555-123-4567"}])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok

    def test_unknown_predicate_flagged(self):
        bundle = _bundle(relations=[_triple(predicate="HACKS_INTO")])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok
        assert any("Unknown predicate" in n for n in notes)

    def test_all_allowed_predicates_pass(self):
        for pred in ALLOWED_PREDICATES:
            bundle = _bundle(relations=[_triple(predicate=pred)])
            ok, notes = stage_2_heuristics(bundle)
            assert ok, f"Predicate {pred} should be allowed but was rejected: {notes}"

    def test_sensitive_in_relation_object(self):
        bundle = _bundle(relations=[_triple(obj="token=abc123def456ghi789")])
        ok, notes = stage_2_heuristics(bundle)
        assert not ok

    def test_clean_technical_terms_pass(self):
        """Ensure legitimate technical terms don't trigger false positives."""
        entities = [
            {"name": "Python"},
            {"name": "Flask Web Framework"},
            {"name": "Neo4j Graph Database"},
            {"name": "HTTPS Protocol"},
            {"name": "OAuth 2.0"},
        ]
        bundle = _bundle(entities=entities)
        ok, notes = stage_2_heuristics(bundle)
        assert ok, f"False positive on technical terms: {notes}"


# ─── Full Pipeline ───────────────────────────────────────────────────────────

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_clean_bundle_passes_all(self):
        bundle = _bundle(
            entities=[{"name": "Python", "type": "Language"}],
            relations=[_triple()],
        )
        result = await run_pre_audit(bundle)
        assert result.passed
        assert result.syntax_ok
        assert result.heuristic_ok

    @pytest.mark.asyncio
    async def test_syntax_failure_skips_heuristics(self):
        bundle = _bundle(origin="")
        result = await run_pre_audit(bundle)
        assert not result.passed
        assert not result.syntax_ok
        assert not result.heuristic_ok  # Skipped

    @pytest.mark.asyncio
    async def test_pii_fails_pipeline(self):
        bundle = _bundle(entities=[{"name": "user@evil.com"}])
        result = await run_pre_audit(bundle)
        assert not result.passed
        assert result.syntax_ok
        assert not result.heuristic_ok
