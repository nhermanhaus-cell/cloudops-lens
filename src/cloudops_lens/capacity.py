from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from cloudops_lens.config import LAMBDA_API_BASE_URL, PRIVATE_CAPACITY_DIR


class CapacityUnavailable(RuntimeError):
    """A sanitized capacity-source failure safe to display to users."""


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def offering_key(gpu_description: str, gpu_count: int) -> str:
    return f"{_slugify(gpu_description)}-{gpu_count}x"


def _data(payload: Any, source: str) -> Any:
    if not isinstance(payload, dict) or "data" not in payload:
        raise CapacityUnavailable(f"Lambda returned an unexpected {source} response shape.")
    return payload["data"]


def normalize_capacity_payloads(
    regions_payload: dict[str, Any],
    instance_types_payload: dict[str, Any],
    snapshot_at: datetime | None = None,
) -> dict[str, Any]:
    captured_at = snapshot_at or datetime.now(UTC)
    region_items = _data(regions_payload, "regions")
    instance_items = _data(instance_types_payload, "instance types")
    if not isinstance(region_items, list) or not region_items:
        raise CapacityUnavailable("Lambda returned no regions.")
    if isinstance(instance_items, dict):
        iterable = list(instance_items.items())
    elif isinstance(instance_items, list):
        iterable = [(None, item) for item in instance_items]
    else:
        raise CapacityUnavailable("Lambda returned no instance types.")

    regions_by_name: dict[str, dict[str, Any]] = {}
    for region in region_items:
        if not isinstance(region, dict) or not region.get("name"):
            raise CapacityUnavailable("Lambda returned an invalid region record.")
        region_name = str(region["name"])
        if region_name in regions_by_name:
            raise CapacityUnavailable("Lambda returned duplicate region identifiers.")
        regions_by_name[region_name] = {
            "name": region_name,
            "description": str(region.get("description", "")),
            "reported_by_regions_endpoint": True,
            "referenced_by_instance_type": False,
            "availability_only": False,
        }
    api_region_count = len(regions_by_name)

    offerings: list[dict[str, Any]] = []
    reported_regions_by_instance_type: dict[str, set[str]] = {}
    seen_instance_types: set[str] = set()
    skipped_non_gpu = 0
    skipped_invalid = 0
    malformed_availability_region_records = 0
    for mapping_name, item in iterable:
        if not isinstance(item, dict):
            skipped_invalid += 1
            continue
        available_names: set[str] = set()
        availability_items = item.get("regions_with_capacity_available", [])
        if not isinstance(availability_items, list):
            malformed_availability_region_records += 1
            availability_items = []
        for region in availability_items:
            if not isinstance(region, dict) or not region.get("name"):
                malformed_availability_region_records += 1
                continue
            region_name = str(region["name"])
            available_names.add(region_name)
            if region_name not in regions_by_name:
                regions_by_name[region_name] = {
                    "name": region_name,
                    "description": str(region.get("description", "")),
                    "reported_by_regions_endpoint": False,
                    "referenced_by_instance_type": True,
                    "availability_only": True,
                }
            else:
                regions_by_name[region_name]["referenced_by_instance_type"] = True
        instance_type = item.get("instance_type", item)
        if not isinstance(instance_type, dict):
            skipped_invalid += 1
            continue
        source_name = str(instance_type.get("name") or mapping_name or "")
        specs = instance_type.get("specs") or {}
        if not source_name or not isinstance(specs, dict):
            skipped_invalid += 1
            continue
        if source_name in seen_instance_types:
            raise CapacityUnavailable("Lambda returned duplicate instance-type identifiers.")
        seen_instance_types.add(source_name)
        try:
            gpu_count = int(specs.get("gpus") or 0)
            vcpus = int(specs.get("vcpus") or 0)
            memory_gib = float(specs.get("memory_gib") or 0)
            storage_gib = float(specs.get("storage_gib") or 0)
            price_cents_per_hour = int(instance_type.get("price_cents_per_hour") or 0)
        except (TypeError, ValueError):
            skipped_invalid += 1
            continue
        if gpu_count <= 0:
            skipped_non_gpu += 1
            continue
        gpu_description = str(instance_type.get("gpu_description") or "").strip()
        if not gpu_description:
            skipped_invalid += 1
            continue
        key = offering_key(gpu_description, gpu_count)
        offerings.append(
            {
                "offering_key": key,
                "source_instance_type": source_name,
                "gpu_description": gpu_description,
                "gpu_count": gpu_count,
                "vcpus": vcpus,
                "memory_gib": memory_gib,
                "storage_gib": storage_gib,
                "price_cents_per_hour": price_cents_per_hour,
            }
        )
        reported_regions_by_instance_type[source_name] = available_names
    if not offerings:
        raise CapacityUnavailable("Lambda returned no instance types.")

    regions = [regions_by_name[name] for name in sorted(regions_by_name)]
    region_names = set(regions_by_name)
    availability: list[dict[str, Any]] = []
    for offering in offerings:
        source_name = offering["source_instance_type"]
        available_names = reported_regions_by_instance_type[source_name]
        for region_name in sorted(region_names):
            reported_available = region_name in available_names
            availability.append(
                {
                    "offering_key": offering["offering_key"],
                    "source_instance_type": source_name,
                    "region_name": region_name,
                    # Retained for backward compatibility with existing private snapshots.
                    "available": reported_available,
                    "reported_available": reported_available,
                    "availability_status": (
                        "reported_available"
                        if reported_available
                        else "not_reported_available"
                    ),
                }
            )
    reported_assignments = sum(row["reported_available"] for row in availability)
    availability_only_regions = [
        region["name"] for region in regions if region["availability_only"]
    ]
    return {
        "snapshot_at": captured_at.isoformat().replace("+00:00", "Z"),
        "source_kind": "authenticated_live",
        "regions": regions,
        "offerings": offerings,
        "availability": availability,
        "normalization_summary": {
            "api_regions_returned": api_region_count,
            "gpu_instance_types_retained": len(offerings),
            "reported_available_assignments": reported_assignments,
            "comparison_rows_created": len(availability),
            "availability_only_regions": availability_only_regions,
            "skipped_non_gpu_instance_types": skipped_non_gpu,
            "skipped_invalid_instance_types": skipped_invalid,
            "malformed_availability_region_records": malformed_availability_region_records,
        },
    }


def fetch_capacity_snapshot(
    api_key: str | None,
    session: requests.Session | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not api_key:
        raise CapacityUnavailable("Lambda API key is not configured.")
    client = session or requests.Session()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "CloudOps-Lens/0.2 interview prototype",
    }

    def get(endpoint: str) -> dict[str, Any]:
        try:
            response = client.get(f"{LAMBDA_API_BASE_URL}/{endpoint}", headers=headers, timeout=30)
        except requests.RequestException as error:
            raise CapacityUnavailable("Lambda capacity API is currently unreachable.") from error
        if response.status_code in {401, 403}:
            raise CapacityUnavailable("Lambda rejected the configured API credentials.")
        if response.status_code == 429:
            raise CapacityUnavailable("Lambda capacity API rate limit was reached.")
        try:
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            raise CapacityUnavailable("Lambda returned an invalid capacity response.") from error

    regions_payload = get("regions")
    sleep_fn(1.05)
    instance_types_payload = get("instance-types")
    return normalize_capacity_payloads(regions_payload, instance_types_payload)


def refresh_private_capacity(api_key: str | None = None) -> dict[str, Any]:
    key = api_key or os.getenv("LAMBDA_API_KEY")
    snapshot = fetch_capacity_snapshot(key)
    captured_at = datetime.fromisoformat(snapshot["snapshot_at"].replace("Z", "+00:00"))
    snapshot_id = captured_at.strftime("%Y%m%dT%H%M%SZ")
    target = PRIVATE_CAPACITY_DIR / f"{snapshot_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(json.dumps(snapshot, indent=2) + "\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "snapshot_id": snapshot_id,
        "path": str(target),
        "regions": len(snapshot["regions"]),
        "offerings": len(snapshot["offerings"]),
        "availability_rows": len(snapshot["availability"]),
    }


def load_private_capacity(paths: tuple[Path, ...]) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in paths]
