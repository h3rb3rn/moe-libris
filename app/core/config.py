"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Nexus server configuration."""

    # Database
    database_url: str = "postgresql+asyncpg://nexus:nexus@localhost:5432/nexus"

    # Neo4j (Global Knowledge Graph)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    # Valkey (Rate limiting, strike counters)
    valkey_url: str = "redis://localhost:6379/0"

    # Instance identity
    nexus_node_id: str = ""
    nexus_public_url: str = ""
    nexus_admin_key: str = ""

    # Registry (server discovery)
    registry_repo_url: str = "https://github.com/moe-sovereign/moe-nexus-registry.git"
    registry_sync_interval: int = 3600  # seconds

    # Abuse prevention
    strike_soft_limit: int = 3       # → rate limit
    strike_hard_limit: int = 10      # → 24h soft-block
    strike_window_seconds: int = 86400  # 24 hours

    # LLM triage (optional, v1.1)
    llm_triage_enabled: bool = False
    llm_triage_endpoint: str = ""
    llm_triage_model: str = ""

    # Logging
    log_level: str = "info"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
