from __future__ import annotations

from pathlib import Path

import duckdb

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
