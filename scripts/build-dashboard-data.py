#!/usr/bin/env python3
"""Convert the capacity test CSV into JSON data for the dashboard.

Generates two files in DATA_DIR:
  - latest.json  : current state (summary + per-region detail)
  - history.json : time series of summaries (appends a point on each run)

Environment variables:
  RESULTS_CSV  path to the CSV produced by the test script
               (default: containerapp-capacity-results.csv)
  DATA_DIR     output directory for the JSON files (default: dashboard/data)
  MAX_HISTORY  maximum number of points kept (default: 336 = 14d hourly)
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone

STATUSES = ("OK", "CAPACITY", "ERROR", "TIMEOUT")


def read_csv(path: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "status": (row.get("status") or "").strip(),
                    "region": (row.get("region") or "").strip(),
                    "detail": (row.get("detail") or "").strip(),
                }
            )
    rows.sort(key=lambda item: (item["status"], item["region"]))
    return rows


def summarize(rows: list[dict[str, str]]) -> dict[str, int]:
    summary = {status: 0 for status in STATUSES}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    summary["total"] = len(rows)
    return summary


def load_history(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def write_json(path: str, payload) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    csv_path = os.environ.get("RESULTS_CSV", "containerapp-capacity-results.csv")
    data_dir = os.environ.get("DATA_DIR", "dashboard/data")
    max_history = int(os.environ.get("MAX_HISTORY", "336"))

    os.makedirs(data_dir, exist_ok=True)
    rows = read_csv(csv_path)
    summary = summarize(rows)
    now = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    write_json(
        os.path.join(data_dir, "latest.json"),
        {"generated_at": now, "summary": summary, "regions": rows},
    )

    history_path = os.path.join(data_dir, "history.json")
    history = load_history(history_path)
    history.append({"timestamp": now, **summary})
    history = history[-max_history:]
    write_json(history_path, history)

    print(f"latest.json + history.json written to {data_dir} ({len(history)} points).")
    print(f"Summary: {summary}")


if __name__ == "__main__":
    main()
