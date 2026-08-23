from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import requests

from cloudops_lens.config import (
    GITHUB_EVENT_DIR,
    GITHUB_EVENTS_URL,
    GITHUB_REPO_DIR,
    GITHUB_REPOS_URL,
    INCIDENT_DIR,
    INCIDENTS_URL,
    PRICING_DIR,
    PRICING_URL,
    REGION_DIR,
    REGIONS_DOC_URL,
    SnapshotPaths,
)
from cloudops_lens.parsers import parse_pricing_html, parse_region_metadata_html


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


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "CloudOps-Lens/0.2 public-data interview prototype",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_github_pages(client: requests.Session, url: str, max_pages: int) -> list[dict]:
    rows: list[dict] = []
    next_url: str | None = url
    pages = 0
    while next_url and pages < max_pages:
        response = client.get(
            next_url,
            params={"per_page": 100} if pages == 0 else None,
            headers=_github_headers(),
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("GitHub response must contain a list")
        rows.extend(payload)
        next_url = response.links.get("next", {}).get("url")
        pages += 1
    return rows


def validate_github_repositories(rows: list[dict]) -> None:
    if not rows:
        raise ValueError("GitHub repository snapshot is empty")
    required = {"id", "name", "full_name", "html_url", "created_at", "updated_at"}
    for row in rows:
        if required - row.keys():
            raise ValueError("GitHub repository response changed shape")


def validate_github_events(rows: list[dict]) -> None:
    required = {"id", "type", "created_at", "repo"}
    for row in rows:
        if required - row.keys():
            raise ValueError("GitHub event response changed shape")


def refresh_sources(session: requests.Session | None = None) -> dict[str, object]:
    client = session or requests.Session()
    headers = {"User-Agent": "CloudOps-Lens/0.1 public-data interview prototype"}
    incident_response = client.get(INCIDENTS_URL, headers=headers, timeout=30)
    incident_response.raise_for_status()
    pricing_response = client.get(PRICING_URL, headers=headers, timeout=30)
    pricing_response.raise_for_status()
    regions_response = client.get(REGIONS_DOC_URL, headers=headers, timeout=30)
    regions_response.raise_for_status()

    payload = incident_response.json()
    incident_count, update_count = validate_incidents(payload)
    price_rows = parse_pricing_html(pricing_response.text)
    region_rows = parse_region_metadata_html(regions_response.text)
    repositories = _fetch_github_pages(client, GITHUB_REPOS_URL, max_pages=10)
    events = _fetch_github_pages(client, GITHUB_EVENTS_URL, max_pages=3)
    validate_github_repositories(repositories)
    validate_github_events(events)

    snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    incident_path = INCIDENT_DIR / f"{snapshot_id}.json"
    pricing_path = PRICING_DIR / f"{snapshot_id}.html"
    regions_path = REGION_DIR / f"{snapshot_id}.html"
    repositories_path = GITHUB_REPO_DIR / f"{snapshot_id}.json"
    events_path = GITHUB_EVENT_DIR / f"{snapshot_id}.json"

    created: list[Path] = []
    try:
        _atomic_write(
            incident_path,
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode(),
        )
        created.append(incident_path)
        _atomic_write(pricing_path, pricing_response.content)
        created.append(pricing_path)
        _atomic_write(regions_path, regions_response.content)
        created.append(regions_path)
        _atomic_write(
            repositories_path,
            (json.dumps(repositories, indent=2, ensure_ascii=False) + "\n").encode(),
        )
        created.append(repositories_path)
        _atomic_write(
            events_path,
            (json.dumps(events, indent=2, ensure_ascii=False) + "\n").encode(),
        )
        created.append(events_path)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise

    return {
        "snapshot": SnapshotPaths(
            snapshot_id,
            incident_path,
            pricing_path,
            regions_path,
            (repositories_path,),
            (events_path,),
        ),
        "incidents": incident_count,
        "updates": update_count,
        "prices": len(price_rows),
        "regions": len(region_rows),
        "github_repositories": len(repositories),
        "github_events": len(events),
    }
