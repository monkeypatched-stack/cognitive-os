"""Neo4j Capability — graph database integration."""

from __future__ import annotations

from typing import Any
from cerebellum.capability import Capability


class Neo4jCapability(Capability):
    """Neo4j integration capability."""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "",
    ):
        super().__init__(name="neo4j")
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = None

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Execute Neo4j query."""
        from neo4j import AsyncGraphDatabase

        if not self._driver:
            self._driver = AsyncGraphDatabase.driver(self._uri, auth=(self._user, self._password))

        query = state.get("query", "")
        parameters = state.get("parameters", {})

        async with self._driver.session() as session:
            result = await session.run(query, parameters)
            records = [dict(record) async for record in result]
            return {"results": records, "count": len(records)}
