from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import requests

from cloudops_lens.config import (
    INCIDENT_DIR,
    INCIDENTS_URL,
    PRICING_DIR,
    PRICING_URL,
    SnapshotPaths,
)
from cloudops_lens.parsers import parse_pricing_html


def validate_incidents(payload: dict) -> tuple[int, int]:
    incidents = payload.get("incidents")
    if not isinstance(incidents, list) or not incidents:
        raise ValueError("Incident response must contain a non-empty incidents list")
    required = {"id", "name", "status", "created_at", "incident_updates"}
    for incident in incidents:
        missing = required - incident.keys()
        if missing:
            raise ValueError(f"Incident {incident.get('id', '<unknown>')} is missing {missing}")
        if not isinstance(incident["incident_updates"], list):
            raise ValueError(f"Incident {incident['id']} has invalid incident_updates")
    updates = sum(len(incident["incident_updates"]) for incident in incidents)
    return len(incidents), updates


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def refresh_sources(session: requests.Session | None = None) -> dict[str, object]:
    client = session or requests.Session()
    headers = {"User-Agent": "CloudOps-Lens/0.1 public-data interview prototype"}
    incident_response = client.get(INCIDENTS_URL, headers=headers, timeout=30)
    incident_response.raise_for_status()
    pricing_response = client.get(PRICING_URL, headers=headers, timeout=30)
    pricing_response.raise_for_status()

    payload = incident_response.json()
    incident_count, update_count = validate_incidents(payload)
    price_rows = parse_pricing_html(pricing_response.text)

    snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    incident_path = INCIDENT_DIR / f"{snapshot_id}.json"
    pricing_path = PRICING_DIR / f"{snapshot_id}.html"

    _atomic_write(
        incident_path,
        (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode(),
    )
    try:
        _atomic_write(pricing_path, pricing_response.content)
    except Exception:
        incident_path.unlink(missing_ok=True)
        raise

    return {
        "snapshot": SnapshotPaths(snapshot_id, incident_path, pricing_path),
        "incidents": incident_count,
        "updates": update_count,
        "prices": len(price_rows),
    }
