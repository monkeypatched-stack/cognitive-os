"""InfluxDB Capability — time series database integration."""

from __future__ import annotations

from typing import Any
from cerebellum.capability import Capability


class InfluxDBCapability(Capability):
    """InfluxDB integration capability."""

    def __init__(self, url: str = "http://localhost:8086", token: str = "", org: str = "agentos"):
        super().__init__(name="influxdb")
        self._url = url
        self._token = token
        self._org = org

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Execute InfluxDB query."""
        from influxdb_client import InfluxDBClient

        client = InfluxDBClient(url=self._url, token=self._token, org=self._org)
        query_api = client.query_api()

        query = state.get("query", "")
        result = query_api.query(query)

        records = []
        for table in result:
            for record in table.records:
                records.append(
                    {
                        "time": record.get_time().isoformat(),
                        "measurement": record.get_measurement(),
                        "fields": record.values,
                    }
                )

        return {"results": records, "count": len(records)}
