import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.auth.helpers import nats_store
from services.common.config import settings


def _is_missing_table(status_code: int, detail: str) -> bool:
    normalized = detail.lower()
    return status_code == 400 and "table" in normalized and "not found" in normalized


def _query_sensor_table(sensor_id: str, limit: int) -> list[dict]:
    table = nats_store.sensor_subject(sensor_id)
    query = (
        "SELECT time, reading_json "
        f'FROM "{table}" '
        "ORDER BY time DESC "
        f"LIMIT {limit}"
    )
    params = urlencode({"db": settings.INFLUXDB_BUCKET, "q": query})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/query_sql?{params}"
    headers = {}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if _is_missing_table(exc.code, detail):
            return []
        raise RuntimeError(
            f"InfluxDB query failed with status {exc.code}: {detail}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Unable to connect to InfluxDB at {settings.INFLUXDB_URL}: {exc}"
        ) from exc


def _reading_from_row(row: dict) -> dict:
    reading_json = row.get("reading_json")
    if isinstance(reading_json, str):
        try:
            reading = json.loads(reading_json)
        except json.JSONDecodeError:
            reading = {}
    else:
        reading = {}
    if row.get("time") is not None:
        reading["time"] = row["time"]
    return reading


async def get_sensor_readings(sensor_id: str, limit: int = 100) -> list[dict]:
    rows = _query_sensor_table(sensor_id, limit)
    return [_reading_from_row(row) for row in rows]


async def get_latest_sensor_reading(sensor_id: str) -> dict | None:
    readings = await get_sensor_readings(sensor_id, limit=1)
    return readings[0] if readings else None
