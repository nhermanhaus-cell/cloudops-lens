from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from cloudops_lens.capacity import load_private_capacity
from cloudops_lens.config import (
    GITHUB_EVENTS_URL,
    GITHUB_REPOS_URL,
    INCIDENTS_URL,
    LAMBDA_API_BASE_URL,
    PRICING_URL,
    REGIONS_DOC_URL,
    SQL_DIR,
    SnapshotPaths,
    latest_snapshot,
)
from cloudops_lens.parsers import (
    classify_themes,
    extract_regions,
    extract_severity,
    github_event_category,
    parse_pricing_html,
    parse_region_metadata_html,
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
        CREATE TABLE raw_region_metadata (
            snapshot_at TIMESTAMPTZ, region_name VARCHAR, physical_location VARCHAR,
            country VARCHAR, geographic_group VARCHAR
        );
        CREATE TABLE raw_github_repository_snapshot (
            snapshot_at TIMESTAMPTZ, repository_id BIGINT, name VARCHAR,
            full_name VARCHAR, html_url VARCHAR, description VARCHAR,
            is_fork BOOLEAN, is_archived BOOLEAN, is_disabled BOOLEAN,
            created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ, pushed_at TIMESTAMPTZ,
            stargazers_count INTEGER, forks_count INTEGER, open_issues_pr_count INTEGER,
            language VARCHAR, license_spdx VARCHAR, default_branch VARCHAR, visibility VARCHAR
        );
        CREATE TABLE raw_github_event (
            source_snapshot_at TIMESTAMPTZ, event_id VARCHAR, event_type VARCHAR,
            event_category VARCHAR, event_created_at TIMESTAMPTZ, repository_name VARCHAR,
            event_action VARCHAR, push_commit_count INTEGER
        );
        CREATE TABLE raw_capacity_offering (
            snapshot_at TIMESTAMPTZ, offering_key VARCHAR, source_instance_type VARCHAR,
            gpu_description VARCHAR, gpu_count INTEGER, vcpus INTEGER,
            memory_gib DOUBLE, storage_gib DOUBLE, price_cents_per_hour INTEGER,
            source_kind VARCHAR
        );
        CREATE TABLE raw_instance_availability (
            snapshot_at TIMESTAMPTZ, offering_key VARCHAR, source_instance_type VARCHAR,
            region_name VARCHAR, available BOOLEAN, source_kind VARCHAR
        );
        CREATE TABLE raw_source_metadata (
            source_name VARCHAR, snapshot_id VARCHAR, snapshot_at TIMESTAMPTZ,
            source_url VARCHAR, row_count INTEGER, coverage_start TIMESTAMPTZ,
            coverage_end TIMESTAMPTZ, source_kind VARCHAR
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
        incident_dates = [
            parse_date
            for incident in payload["incidents"]
            if (parse_date := incident.get("created_at"))
        ]
        connection.execute(
            "INSERT INTO raw_source_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "lambda_status_incidents",
                snapshot.snapshot_id,
                snapshot_at,
                INCIDENTS_URL,
                incident_count,
                min(incident_dates) if incident_dates else None,
                max(incident_dates) if incident_dates else None,
                "public",
            ],
        )
        connection.execute(
            "INSERT INTO raw_source_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "lambda_gpu_pricing",
                snapshot.snapshot_id,
                snapshot_at,
                PRICING_URL,
                len(price_rows),
                snapshot_at,
                snapshot_at,
                "public",
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {"incidents": incident_count, "updates": update_count, "prices": len(price_rows)}


def _load_region_metadata(connection: duckdb.DuckDBPyConnection, snapshot: SnapshotPaths) -> int:
    if snapshot.regions is None:
        return 0
    rows = parse_region_metadata_html(snapshot.regions.read_text())
    snapshot_at = _snapshot_timestamp(snapshot.regions.stem)
    connection.executemany(
        "INSERT INTO raw_region_metadata VALUES (?, ?, ?, ?, ?)",
        [
            (
                snapshot_at,
                row.region_name,
                row.physical_location,
                row.country,
                row.geographic_group,
            )
            for row in rows
        ],
    )
    connection.execute(
        "INSERT INTO raw_source_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "lambda_region_documentation",
            snapshot.regions.stem,
            snapshot_at,
            REGIONS_DOC_URL,
            len(rows),
            snapshot_at,
            snapshot_at,
            "public",
        ],
    )
    return len(rows)


def _load_github_snapshots(
    connection: duckdb.DuckDBPyConnection, snapshot: SnapshotPaths
) -> tuple[int, int]:
    repository_rows = 0
    event_rows = 0
    for path in snapshot.github_repositories:
        captured_at = _snapshot_timestamp(path.stem)
        repositories = json.loads(path.read_text())
        values = []
        for repository in repositories:
            license_value = repository.get("license") or {}
            values.append(
                (
                    captured_at,
                    repository["id"],
                    repository["name"],
                    repository["full_name"],
                    repository["html_url"],
                    repository.get("description"),
                    bool(repository.get("fork")),
                    bool(repository.get("archived")),
                    bool(repository.get("disabled")),
                    repository.get("created_at"),
                    repository.get("updated_at"),
                    repository.get("pushed_at"),
                    int(repository.get("stargazers_count") or 0),
                    int(repository.get("forks_count") or 0),
                    int(repository.get("open_issues_count") or 0),
                    repository.get("language"),
                    license_value.get("spdx_id"),
                    repository.get("default_branch"),
                    repository.get("visibility"),
                )
            )
        if values:
            connection.executemany(
                "INSERT INTO raw_github_repository_snapshot VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        created_dates = [row.get("created_at") for row in repositories if row.get("created_at")]
        updated_dates = [row.get("updated_at") for row in repositories if row.get("updated_at")]
        connection.execute(
            "INSERT INTO raw_source_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "github_repositories",
                path.stem,
                captured_at,
                GITHUB_REPOS_URL,
                len(values),
                min(created_dates) if created_dates else None,
                max(updated_dates) if updated_dates else None,
                "public",
            ],
        )
        repository_rows += len(values)

    for path in snapshot.github_events:
        captured_at = _snapshot_timestamp(path.stem)
        events = json.loads(path.read_text())
        values = []
        for event in events:
            payload = event.get("payload") or {}
            repository = event.get("repo") or {}
            values.append(
                (
                    captured_at,
                    str(event["id"]),
                    event["type"],
                    github_event_category(event["type"]),
                    event.get("created_at"),
                    repository.get("name"),
                    payload.get("action"),
                    int(payload.get("size") or 0),
                )
            )
        if values:
            connection.executemany(
                "INSERT INTO raw_github_event VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values
            )
        event_dates = [event.get("created_at") for event in events if event.get("created_at")]
        connection.execute(
            "INSERT INTO raw_source_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "github_events",
                path.stem,
                captured_at,
                GITHUB_EVENTS_URL,
                len(values),
                min(event_dates) if event_dates else None,
                max(event_dates) if event_dates else None,
                "public_recent_window",
            ],
        )
        event_rows += len(values)
    return repository_rows, event_rows


def _load_capacity_snapshots(connection: duckdb.DuckDBPyConnection, snapshot: SnapshotPaths) -> int:
    availability_rows = 0
    for payload in load_private_capacity(snapshot.private_capacity):
        captured_at = datetime.fromisoformat(payload["snapshot_at"].replace("Z", "+00:00"))
        source_kind = payload.get("source_kind", "authenticated_private")
        offerings = [
            (
                captured_at,
                row["offering_key"],
                row["source_instance_type"],
                row["gpu_description"],
                row["gpu_count"],
                row["vcpus"],
                row["memory_gib"],
                row["storage_gib"],
                row["price_cents_per_hour"],
                source_kind,
            )
            for row in payload["offerings"]
        ]
        availability = [
            (
                captured_at,
                row["offering_key"],
                row["source_instance_type"],
                row["region_name"],
                bool(row["available"]),
                source_kind,
            )
            for row in payload["availability"]
        ]
        if offerings:
            connection.executemany(
                "INSERT INTO raw_capacity_offering VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                offerings,
            )
        if availability:
            connection.executemany(
                "INSERT INTO raw_instance_availability VALUES (?, ?, ?, ?, ?, ?)",
                availability,
            )
        connection.execute(
            "INSERT INTO raw_source_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "lambda_instance_capacity",
                captured_at.strftime("%Y%m%dT%H%M%SZ"),
                captured_at,
                f"{LAMBDA_API_BASE_URL}/instance-types",
                len(availability),
                captured_at,
                captured_at,
                source_kind,
            ],
        )
        availability_rows += len(availability)
    return availability_rows


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
        connection.execute("BEGIN TRANSACTION")
        try:
            counts["regions"] = _load_region_metadata(connection, selected)
            repository_rows, event_rows = _load_github_snapshots(connection, selected)
            counts["github_repository_rows"] = repository_rows
            counts["github_event_rows"] = event_rows
            counts["private_capacity_rows"] = _load_capacity_snapshots(connection, selected)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
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
