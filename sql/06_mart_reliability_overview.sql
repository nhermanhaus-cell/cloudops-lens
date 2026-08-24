CREATE TABLE mart_reliability_overview AS
WITH anchor AS (
    SELECT max(snapshot_at) AS as_of_at FROM raw_snapshot_metadata
),
windowed AS (
    SELECT incident.*
    FROM fact_incident AS incident
    CROSS JOIN anchor
    WHERE incident.started_at >= anchor.as_of_at - INTERVAL 90 DAY
      AND incident.started_at <= anchor.as_of_at
)
SELECT
    (SELECT as_of_at FROM anchor) AS as_of_at,
    count(DISTINCT incident_id) AS incidents_90d,
    count(DISTINCT incident_id) FILTER (WHERE severity IN ('high', 'critical'))
        AS high_critical_incidents_90d,
    avg(public_mttr_minutes) FILTER (WHERE is_resolved) AS mean_public_mttr_minutes,
    median(public_mttr_minutes) FILTER (WHERE is_resolved) AS median_public_mttr_minutes,
    quantile_cont(public_mttr_minutes, 0.9) FILTER (WHERE is_resolved)
        AS p90_public_mttr_minutes,
    count(DISTINCT incident_id) FILTER (WHERE NOT is_resolved) AS open_incidents_90d
FROM windowed;
