from __future__ import annotations

import json
from pathlib import Path

import duckdb

from cloudops_lens.config import SnapshotPaths, latest_snapshot
from cloudops_lens.pipeline import build_database


def _rows(path: Path, sql: str) -> list[tuple]:
    with duckdb.connect(str(path), read_only=True) as connection:
        return connection.execute(sql).fetchall()


def test_offline_build_is_deterministic_and_preserves_grain(tmp_path: Path) -> None:
    first = tmp_path / "first.duckdb"
    second = tmp_path / "second.duckdb"
    first_summary = build_database(first)
    second_summary = build_database(second)

    assert first_summary["incidents"] == second_summary["incidents"]
    assert first_summary["updates"] == second_summary["updates"]
    assert first_summary["prices"] == second_summary["prices"]

    comparison_sql = """
        SELECT incident_id, severity, status, started_at, resolved_at, public_mttr_minutes
        FROM fact_incident ORDER BY incident_id
    """
    assert _rows(first, comparison_sql) == _rows(second, comparison_sql)

    with duckdb.connect(str(first), read_only=True) as connection:
        failures = connection.execute(
            "SELECT count(*) FROM mart_data_quality WHERE status = 'fail'"
        ).fetchone()[0]
        assert failures == 0
        raw_count, fact_count = connection.execute(
            "SELECT (SELECT count(*) FROM raw_incident), (SELECT count(*) FROM fact_incident)"
        ).fetchone()
        assert raw_count == fact_count

        multi_region = connection.execute(
            """
            SELECT bridge.incident_id, count(*) AS region_count
            FROM bridge_incident_region AS bridge
            GROUP BY bridge.incident_id
            HAVING count(*) > 1
            ORDER BY region_count DESC
            LIMIT 1
            """
        ).fetchone()
        assert multi_region is not None
        fact_rows = connection.execute(
            "SELECT count(*) FROM fact_incident WHERE incident_id = ?", [multi_region[0]]
        ).fetchone()[0]
        assert fact_rows == 1

        incident_region_count, enriched_region_count = connection.execute(
            """
            SELECT
                (SELECT count(DISTINCT region_name) FROM raw_incident_region),
                (SELECT count(DISTINCT raw.region_name)
                 FROM raw_incident_region AS raw
                 JOIN dim_region AS region USING (region_name))
            """
        ).fetchone()
        assert incident_region_count == enriched_region_count

        open_count = connection.execute(
            """
            SELECT count(*) FROM fact_incident
            WHERE NOT is_resolved AND public_mttr_minutes IS NULL
            """
        ).fetchone()[0]
        assert open_count > 0


def test_price_snapshot_arithmetic() -> None:
    path = Path("data/cloudops_lens.duckdb")
    if not path.exists():
        build_database(path)
    mismatches = _rows(
        path,
        """
        SELECT instance_type
        FROM fact_instance_price_snapshot
        WHERE abs(instance_price_per_hour - gpu_count * price_per_gpu_hour) > 0.0001
        """,
    )
    assert mismatches == []


def test_overlapping_github_event_snapshots_deduplicate_by_event_id(tmp_path: Path) -> None:
    base = latest_snapshot()
    first = tmp_path / "20260821T000000Z.json"
    second = tmp_path / "20260822T000000Z.json"
    shared = {
        "id": "event-1",
        "type": "PushEvent",
        "created_at": "2026-08-20T12:00:00Z",
        "repo": {"name": "LambdaLabsML/example"},
        "payload": {"size": 2},
    }
    first.write_text(json.dumps([shared]))
    second.write_text(
        json.dumps(
            [
                shared,
                {
                    "id": "event-2",
                    "type": "WatchEvent",
                    "created_at": "2026-08-21T12:00:00Z",
                    "repo": {"name": "LambdaLabsML/example"},
                    "payload": {},
                },
            ]
        )
    )
    snapshot = SnapshotPaths(
        snapshot_id=base.snapshot_id,
        incidents=base.incidents,
        pricing=base.pricing,
        regions=base.regions,
        github_events=(first, second),
    )
    database = tmp_path / "deduplicated.duckdb"
    build_database(database, snapshot)
    assert _rows(database, "SELECT count(*) FROM raw_github_event") == [(3,)]
    assert _rows(database, "SELECT count(*) FROM fact_github_event") == [(2,)]


def test_legacy_private_capacity_snapshot_remains_loadable(tmp_path: Path) -> None:
    base = latest_snapshot()
    private_snapshot = tmp_path / "20260824T120000Z.json"
    private_snapshot.write_text(
        json.dumps(
            {
                "snapshot_at": "2026-08-24T12:00:00Z",
                "source_kind": "authenticated_private",
                "regions": [{"name": "us-east-1", "description": "Washington, D.C."}],
                "offerings": [
                    {
                        "offering_key": "nvidia-a100-40-gb-1x",
                        "source_instance_type": "gpu_1x_a100",
                        "gpu_description": "NVIDIA A100 40 GB",
                        "gpu_count": 1,
                        "vcpus": 30,
                        "memory_gib": 200,
                        "storage_gib": 512,
                        "price_cents_per_hour": 129,
                    }
                ],
                # Legacy snapshots contain only `available`, not `reported_available`.
                "availability": [
                    {
                        "offering_key": "nvidia-a100-40-gb-1x",
                        "source_instance_type": "gpu_1x_a100",
                        "region_name": "us-east-1",
                        "available": True,
                    }
                ],
            }
        )
    )
    snapshot = SnapshotPaths(
        snapshot_id=base.snapshot_id,
        incidents=base.incidents,
        pricing=base.pricing,
        regions=base.regions,
        github_repositories=base.github_repositories,
        github_events=base.github_events,
        private_capacity=(private_snapshot,),
    )
    database = tmp_path / "legacy-capacity.duckdb"
    build_database(database, snapshot)
    assert _rows(
        database,
        "SELECT reported_available FROM fact_instance_availability_snapshot",
    ) == [(True,)]
    assert _rows(
        database,
        "SELECT reported_available_pairs FROM mart_capacity_history",
    ) == [(1,)]
