from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from cloudops_lens.config import (
    INCIDENTS_URL,
    PRICING_URL,
    SQL_DIR,
    SnapshotPaths,
    latest_snapshot,
)
from cloudops_lens.parsers import (
    classify_themes,
    extract_regions,
    extract_severity,
    parse_pricing_html,
)
from cloudops_lens.refresh import validate_incidents


def _snapshot_timestamp(snapshot_id: str) -> datetime:
    return datetime.strptime(snapshot_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def _create_raw_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE raw_incident (
            incident_id VARCHAR, title VARCHAR, status VARCHAR, source_impact VARCHAR,
            created_at VARCHAR, updated_at VARCHAR, monitoring_at VARCHAR, resolved_at VARCHAR,
            snapshot_at TIMESTAMPTZ
        );
        CREATE TABLE raw_incident_update (
            incident_update_id VARCHAR, incident_id VARCHAR, update_status VARCHAR,
            created_at VARCHAR, display_at VARCHAR, updated_at VARCHAR, update_text VARCHAR,
            severity_extracted VARCHAR, snapshot_at TIMESTAMPTZ
        );
        CREATE TABLE raw_incident_region (
            incident_id VARCHAR, region_raw VARCHAR, region_name VARCHAR,
            normalization_status VARCHAR, evidence VARCHAR, snapshot_at TIMESTAMPTZ
        );
        CREATE TABLE raw_incident_theme (
            incident_id VARCHAR, theme_slug VARCHAR, theme_name VARCHAR,
            rule_id VARCHAR, evidence VARCHAR, snapshot_at TIMESTAMPTZ
        );
        CREATE TABLE raw_instance_price (
            snapshot_at TIMESTAMPTZ, instance_type VARCHAR, gpu_model VARCHAR,
            gpu_count INTEGER, vram_gb_per_gpu DOUBLE, vcpus INTEGER,
            ram_gib DOUBLE, storage_gib DOUBLE, price_per_gpu_hour DOUBLE,
            instance_price_per_hour DOUBLE, price_per_vram_gb_hour DOUBLE
        );
        CREATE TABLE raw_snapshot_metadata (
            snapshot_at TIMESTAMPTZ, snapshot_id VARCHAR, incidents_source_url VARCHAR,
            pricing_source_url VARCHAR, raw_incident_count INTEGER,
            raw_update_count INTEGER, raw_price_count INTEGER
        );
        """
    )


def _load_raw_tables(
    connection: duckdb.DuckDBPyConnection, snapshot: SnapshotPaths
) -> dict[str, int]:
    payload = json.loads(snapshot.incidents.read_text())
    incident_count, update_count = validate_incidents(payload)
    price_rows = parse_pricing_html(snapshot.pricing.read_text())
    snapshot_at = _snapshot_timestamp(snapshot.snapshot_id)

    incident_rows: list[tuple] = []
    update_rows: list[tuple] = []
    region_rows: list[tuple] = []
    theme_rows: list[tuple] = []

    for incident in payload["incidents"]:
        incident_id = incident["id"]
        incident_rows.append(
            (
                incident_id,
                incident["name"],
                incident["status"],
                incident.get("impact"),
                incident.get("created_at"),
                incident.get("updated_at"),
                incident.get("monitoring_at"),
                incident.get("resolved_at"),
                snapshot_at,
            )
        )
        updates = incident.get("incident_updates", [])
        combined_text = "\n".join(
            [incident["name"], *(update.get("body", "") for update in updates)]
        )
        for update in updates:
            update_rows.append(
                (
                    update["id"],
                    incident_id,
                    update.get("status"),
                    update.get("created_at"),
                    update.get("display_at"),
                    update.get("updated_at"),
                    update.get("body", ""),
                    extract_severity(update.get("body", "")),
                    snapshot_at,
                )
            )
        for region in extract_regions(combined_text):
            region_rows.append(
                (
                    incident_id,
                    region.raw,
                    region.canonical,
                    region.normalization_status,
                    region.raw,
                    snapshot_at,
                )
            )
        for theme in classify_themes(combined_text):
            theme_rows.append(
                (
                    incident_id,
                    theme.slug,
                    theme.name,
                    theme.rule_id,
                    theme.evidence,
                    snapshot_at,
                )
            )

    price_values = [
        (
            snapshot_at,
            row.instance_type,
            row.gpu_model,
            row.gpu_count,
            row.vram_gb_per_gpu,
            row.vcpus,
            row.ram_gib,
            row.storage_gib,
            row.price_per_gpu_hour,
            row.instance_price_per_hour,
            row.price_per_vram_gb_hour,
        )
        for row in price_rows
    ]

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.executemany(
            "INSERT INTO raw_incident VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", incident_rows
        )
        connection.executemany(
            "INSERT INTO raw_incident_update VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", update_rows
        )
        if region_rows:
            connection.executemany(
                "INSERT INTO raw_incident_region VALUES (?, ?, ?, ?, ?, ?)", region_rows
            )
        if theme_rows:
            connection.executemany(
                "INSERT INTO raw_incident_theme VALUES (?, ?, ?, ?, ?, ?)", theme_rows
            )
        connection.executemany(
            "INSERT INTO raw_instance_price VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            price_values,
        )
        connection.execute(
            "INSERT INTO raw_snapshot_metadata VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                snapshot_at,
                snapshot.snapshot_id,
                INCIDENTS_URL,
                PRICING_URL,
                incident_count,
                update_count,
                len(price_rows),
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {"incidents": incident_count, "updates": update_count, "prices": len(price_rows)}


def _run_sql_models(connection: duckdb.DuckDBPyConnection) -> None:
    scripts = sorted(SQL_DIR.glob("*.sql"))
    if not scripts:
        raise FileNotFoundError(f"No SQL models found in {SQL_DIR}")
    for script in scripts:
        connection.execute(script.read_text())


def build_database(
    output_path: str | Path, snapshot: SnapshotPaths | None = None
) -> dict[str, object]:
    """Atomically build a DuckDB file from one complete local source snapshot."""
    selected = snapshot or latest_snapshot()
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".duckdb", dir=target.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink(missing_ok=True)
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(temporary_path))
        _create_raw_tables(connection)
        counts = _load_raw_tables(connection, selected)
        _run_sql_models(connection)
        quality_failures = connection.execute(
            "SELECT COUNT(*) FROM mart_data_quality WHERE status = 'fail'"
        ).fetchone()[0]
        if quality_failures:
            failures = connection.execute(
                "SELECT check_name, record_count FROM mart_data_quality WHERE status = 'fail'"
            ).fetchall()
            raise ValueError(f"Blocking data quality checks failed: {failures}")
        connection.execute("CHECKPOINT")
        connection.close()
        connection = None
        os.replace(temporary_path, target)
    finally:
        if connection is not None:
            connection.close()
        temporary_path.unlink(missing_ok=True)
    return {"database": str(target), "snapshot_id": selected.snapshot_id, **counts}
