"""Neo4j Global Knowledge Graph service for approved triples."""

from datetime import datetime, timezone

from neo4j import AsyncGraphDatabase, AsyncDriver

from app.core.config import settings

_driver: AsyncDriver | None = None


async def get_driver() -> AsyncDriver:
    """Get or create the Neo4j async driver."""
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


async def close_driver() -> None:
    """Close the Neo4j driver."""
    global _driver
    if _driver:
        await _driver.close()
        _driver = None


async def init_schema() -> None:
    """Create indexes and constraints on the global graph."""
    driver = await get_driver()
    async with driver.session() as session:
        await session.run(
            "CREATE CONSTRAINT entity_name IF NOT EXISTS "
            "FOR (e:Entity) REQUIRE e.name IS UNIQUE"
        )
        await session.run(
            "CREATE INDEX entity_domain IF NOT EXISTS FOR (e:Entity) ON (e.domain)"
        )
        await session.run(
            "CREATE INDEX entity_origin IF NOT EXISTS FOR (e:Entity) ON (e.origin_node_id)"
        )
        await session.run(
            "CREATE INDEX entity_approved IF NOT EXISTS FOR (e:Entity) ON (e.approved_at)"
        )


async def commit_bundle(bundle_data: dict, origin_node_id: str) -> dict:
    """Commit an approved bundle to the global Neo4j graph.

    Returns stats: {entities_created, relations_created}.
    """
    driver = await get_driver()
    now = datetime.now(timezone.utc).isoformat()
    entities_created = 0
    relations_created = 0

    async with driver.session() as session:
        # Commit entities
        for entity in bundle_data.get("entities", []):
            result = await session.run(
                """
                MERGE (e:Entity {name: $name})
                ON CREATE SET
                    e.type = $type,
                    e.domain = $domain,
                    e.origin_node_id = $origin,
                    e.approved_at = $approved_at,
                    e.source = 'federation'
                RETURN e.name AS name,
                       CASE WHEN e.approved_at = $approved_at THEN true ELSE false END AS created
                """,
                name=entity.get("name", ""),
                type=entity.get("type", "Unknown"),
                domain=entity.get("domain", "general"),
                origin=origin_node_id,
                approved_at=now,
            )
            record = await result.single()
            if record and record["created"]:
                entities_created += 1

        # Commit relations
        for rel in bundle_data.get("relations", []):
            result = await session.run(
                """
                MERGE (s:Entity {name: $subject})
                ON CREATE SET s.type = $s_type, s.source = 'federation',
                              s.origin_node_id = $origin, s.approved_at = $approved_at
                MERGE (o:Entity {name: $object})
                ON CREATE SET o.type = $o_type, o.source = 'federation',
                              o.origin_node_id = $origin, o.approved_at = $approved_at
                MERGE (s)-[r:""" + rel.get("predicate", "RELATED_TO") + """]->(o)
                ON CREATE SET
                    r.confidence = $confidence,
                    r.domain = $domain,
                    r.origin_node_id = $origin,
                    r.approved_at = $approved_at,
                    r.source = 'federation'
                RETURN type(r) AS rel_type,
                       CASE WHEN r.approved_at = $approved_at THEN true ELSE false END AS created
                """,
                subject=rel.get("subject", ""),
                s_type=rel.get("subject_type", "Unknown"),
                object=rel.get("object", ""),
                o_type=rel.get("object_type", "Unknown"),
                confidence=rel.get("confidence", 0.5),
                domain=rel.get("domain", "general"),
                origin=origin_node_id,
                approved_at=now,
            )
            record = await result.single()
            if record and record["created"]:
                relations_created += 1

    return {
        "entities_created": entities_created,
        "relations_created": relations_created,
    }


async def pull_since(
    since: datetime | None = None,
    domains: list[str] | None = None,
    limit: int = 1000,
) -> dict:
    """Pull approved triples created after a given timestamp.

    Returns a JSON-LD compatible bundle.
    """
    driver = await get_driver()
    entities = []
    relations = []

    async with driver.session() as session:
        # Build Cypher query with filters
        where_clauses = ["e.source = 'federation'"]
        params: dict = {"limit": limit}

        if since:
            where_clauses.append("e.approved_at > $since")
            params["since"] = since.isoformat()

        if domains:
            where_clauses.append("e.domain IN $domains")
            params["domains"] = domains

        where = " AND ".join(where_clauses)

        # Fetch entities
        result = await session.run(
            f"""
            MATCH (e:Entity) WHERE {where}
            RETURN e.name AS name, e.type AS type, e.domain AS domain,
                   e.origin_node_id AS origin, e.approved_at AS approved_at
            ORDER BY e.approved_at ASC
            LIMIT $limit
            """,
            **params,
        )
        async for record in result:
            entities.append({
                "name": record["name"],
                "type": record["type"],
                "domain": record["domain"],
                "origin_node_id": record["origin"],
                "approved_at": record["approved_at"],
            })

        # Fetch relations connected to those entities
        if entities:
            entity_names = [e["name"] for e in entities]
            rel_result = await session.run(
                """
                MATCH (s:Entity)-[r]->(o:Entity)
                WHERE s.name IN $names AND r.source = 'federation'
                RETURN s.name AS subject, s.type AS subject_type,
                       type(r) AS predicate, o.name AS object, o.type AS object_type,
                       r.confidence AS confidence, r.domain AS domain,
                       r.origin_node_id AS origin, r.approved_at AS approved_at
                """,
                names=entity_names,
            )
            async for record in rel_result:
                relations.append({
                    "subject": record["subject"],
                    "subject_type": record["subject_type"],
                    "predicate": record["predicate"],
                    "object": record["object"],
                    "object_type": record["object_type"],
                    "confidence": record["confidence"],
                    "domain": record["domain"],
                    "origin_node_id": record["origin"],
                    "approved_at": record["approved_at"],
                })

    return {
        "@context": "https://moe-sovereign.org/knowledge/v1",
        "entities": entities,
        "relations": relations,
        "total": len(entities) + len(relations),
        "has_more": len(entities) >= limit,
    }


async def get_graph_stats() -> dict:
    """Get global graph statistics."""
    driver = await get_driver()
    async with driver.session() as session:
        entity_count = await session.run("MATCH (e:Entity) RETURN count(e) AS c")
        e_record = await entity_count.single()

        rel_count = await session.run("MATCH ()-[r]->() RETURN count(r) AS c")
        r_record = await rel_count.single()

        return {
            "approved_entities": e_record["c"] if e_record else 0,
            "approved_triples": r_record["c"] if r_record else 0,
        }
