#!/usr/bin/env python3
"""
Script to check if calibration-log data exists in InfluxDB 3.x.
"""

import subprocess
import json
import os
import sys

# InfluxDB Configuration — credentials come from the environment.
INFLUXDB_DATABASE = os.getenv("INFLUXDB_BUCKET", "events")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN", "")

if not INFLUXDB_TOKEN:
    sys.exit("INFLUXDB_TOKEN is not set — export it before running this script.")


def check_calibration_logs():
    """Check for the existence of calibration-log data in InfluxDB 3.x."""
    try:
        # Use influxdb3 CLI to query calibration-log data
        cmd = [
            "/opt/homebrew/opt/influxdb/bin/influxdb3",
            "query",
            "--database",
            INFLUXDB_DATABASE,
            "--token",
            INFLUXDB_TOKEN,
            "--format",
            "json",
            'SELECT COUNT(*) FROM "calibration-log"',
        ]

        print(f"🔍 Querying InfluxDB for 'calibration-log' data...")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            # Parse JSON output
            try:
                data = json.loads(result.stdout)
                if data and len(data) > 0:
                    count = data[0].get("count(*)", 0)
                    if count > 0:
                        print(f"✅ Found {count} data points for 'calibration-log' in InfluxDB.")
                    else:
                        print(f"❌ No data points found for 'calibration-log' in InfluxDB.")
                else:
                    print(f"❌ No data points found for 'calibration-log' in InfluxDB.")
            except json.JSONDecodeError:
                # Fallback to parsing raw output
                if "count" in result.stdout:
                    print(f"✅ Found calibration-log data in InfluxDB.")
                else:
                    print(f"❌ No data points found for 'calibration-log' in InfluxDB.")
        else:
            print(f"❌ Error querying InfluxDB: {result.stderr}")

    except Exception as e:
        print(f"❌ Error connecting to InfluxDB or executing query: {e}")


if __name__ == "__main__":
    check_calibration_logs()
