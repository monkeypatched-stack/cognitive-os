#!/usr/bin/env python3
"""
Script to check if calibration-log data exists in InfluxDB.
"""

from influxdb_client import InfluxDBClient

# InfluxDB Configuration
INFLUXDB_URL = "http://localhost:8181"
INFLUXDB_TOKEN = "indus-token"
INFLUXDB_ORG = "indus"
INFLUXDB_BUCKET = "events"


def check_calibration_logs():
    """Check for the existence of calibration-log data in InfluxDB."""
    try:
        client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        query_api = client.query_api()

        # Flux query to count points in the calibration-log measurement
        query = f'from(bucket: "{INFLUXDB_BUCKET}") |> range(start: -30d) |> filter(fn: (r) => r["_measurement"] == "calibration-log")'

        print(f"🔍 Querying InfluxDB for 'calibration-log' data...")
        result = query_api.query(query)

        count = 0
        for table in result:
            for record in table.records:
                count += 1

        if count > 0:
            print(f"✅ Found {count} data points for 'calibration-log' in InfluxDB.")
        else:
            print(f"❌ No data points found for 'calibration-log' in InfluxDB.")

        client.close()
    except Exception as e:
        print(f"❌ Error connecting to InfluxDB or executing query: {e}")


if __name__ == "__main__":
    check_calibration_logs()
