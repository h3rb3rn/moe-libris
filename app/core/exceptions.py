"""
app/core/exceptions.py — Structured exception hierarchy for moe-libris.

All application exceptions derive from LibrisError so callers can catch the
base class when they don't care which subsystem failed, or a specific subclass
when they need to handle it differently (e.g. AbuseError → 429, AuditError → 422).

Usage:
    from app.core.exceptions import AuditError, GraphError
    raise AuditError("Bundle failed PII check")
"""


class LibrisError(Exception):
    """Base class for all moe-libris application errors."""


class AuditError(LibrisError):
    """Raised when a knowledge bundle fails the pre-audit pipeline."""


class GraphError(LibrisError):
    """Raised when a Neo4j operation fails (connection, query, constraint)."""


class AbuseError(LibrisError):
    """Raised when a federation node exceeds rate limits or strike thresholds."""


class RegistryError(LibrisError):
    """Raised when node registry operations fail (unknown node, blocked node)."""
