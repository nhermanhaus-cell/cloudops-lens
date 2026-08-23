CREATE TABLE mart_data_quality AS
WITH checks AS (
    SELECT 'duplicate_incident_ids' AS check_name, 'fail' AS failure_status,
        count(*)::BIGINT AS record_count,
        'Incident IDs must be unique at the fact grain.' AS detail
    FROM (SELECT incident_id FROM raw_incident GROUP BY incident_id HAVING count(*) > 1)
    UNION ALL
    SELECT 'duplicate_update_ids', 'fail', count(*)::BIGINT,
        'Incident update IDs must be unique.'
    FROM (
        SELECT incident_update_id FROM raw_incident_update
        GROUP BY incident_update_id HAVING count(*) > 1
    )
    UNION ALL
    SELECT 'unknown_severity', 'warn', count(*)::BIGINT,
        'No unambiguous published severity was found.'
    FROM fact_incident WHERE severity = 'unknown'
    UNION ALL
    SELECT 'missing_region', 'warn', count(*)::BIGINT,
        'No explicit public region token was found; no region was inferred.'
    FROM fact_incident AS incident
    LEFT JOIN bridge_incident_region AS bridge USING (incident_id)
    WHERE bridge.incident_id IS NULL
    UNION ALL
    SELECT 'unresolved_incidents', 'info', count(*)::BIGINT,
        'Open incidents are retained and excluded from Public MTTR aggregates.'
    FROM fact_incident WHERE NOT is_resolved
    UNION ALL
    SELECT 'negative_public_mttr', 'fail', count(*)::BIGINT,
        'Resolution cannot precede the first public update.'
    FROM fact_incident WHERE public_mttr_minutes < 0
    UNION ALL
    SELECT 'unrecognized_region_tokens', 'warn', count(*)::BIGINT,
        'Region-like tokens that did not normalize to the expected format.'
    FROM raw_incident_region WHERE normalization_status = 'unrecognized'
    UNION ALL
    SELECT 'region_alias_corrections', 'info', count(*)::BIGINT,
        'Known source spelling variants retained with a documented canonical value.'
    FROM raw_incident_region WHERE normalization_status = 'alias_corrected'
    UNION ALL
    SELECT 'undocumented_incident_regions', 'warn', count(DISTINCT region.region_name)::BIGINT,
        'Incident regions absent from the current public region directory remain visible.'
    FROM bridge_incident_region AS bridge
    JOIN dim_region AS region USING (region_id)
    WHERE NOT region.is_currently_documented
    UNION ALL
    SELECT 'duplicate_region_metadata', 'fail', count(*)::BIGINT,
        'Region codes must be unique within each documentation snapshot.'
    FROM (
        SELECT snapshot_at, region_name
        FROM raw_region_metadata
        GROUP BY snapshot_at, region_name
        HAVING count(*) > 1
    )
    UNION ALL
    SELECT 'orphan_region_bridges', 'fail', count(*)::BIGINT,
        'Every incident-region bridge must reference an incident.'
    FROM bridge_incident_region AS bridge
    LEFT JOIN fact_incident AS incident USING (incident_id)
    WHERE incident.incident_id IS NULL
    UNION ALL
    SELECT 'pricing_arithmetic_mismatch', 'fail', count(*)::BIGINT,
        'Instance hourly price must equal GPU count multiplied by per-GPU price.'
    FROM fact_instance_price_snapshot
    WHERE abs(instance_price_per_hour - gpu_count * price_per_gpu_hour) > 0.0001
    UNION ALL
    SELECT 'duplicate_capacity_grain', 'fail', count(*)::BIGINT,
        'Capacity rows must be unique by snapshot, offering, and region.'
    FROM (
        SELECT snapshot_at, offering_key, source_instance_type, region_id
        FROM fact_instance_availability_snapshot
        GROUP BY snapshot_at, offering_key, source_instance_type, region_id
        HAVING count(*) > 1
    )
    UNION ALL
    SELECT 'duplicate_github_repository_grain', 'fail', count(*)::BIGINT,
        'Repository rows must be unique by snapshot and repository ID.'
    FROM (
        SELECT snapshot_at, repository_id
        FROM fact_github_repository_snapshot
        GROUP BY snapshot_at, repository_id
        HAVING count(*) > 1
    )
    UNION ALL
    SELECT 'duplicate_github_event_ids', 'fail', count(*)::BIGINT,
        'Deduplicated GitHub event IDs must be unique.'
    FROM (
        SELECT event_id FROM fact_github_event
        GROUP BY event_id HAVING count(*) > 1
    )
),
display_checks AS (
    SELECT
        check_name,
        CASE WHEN record_count = 0 THEN 'pass' ELSE failure_status END AS status,
        record_count,
        detail
    FROM checks
),
counts AS (
    SELECT 'raw_incident_rows' AS check_name, 'info' AS status,
        count(*)::BIGINT AS record_count, 'Rows loaded from the incident snapshot.' AS detail
    FROM raw_incident
    UNION ALL
    SELECT 'transformed_incident_rows', 'info', count(*)::BIGINT,
        'Rows at the one-incident fact grain.' FROM fact_incident
    UNION ALL
    SELECT 'raw_update_rows', 'info', count(*)::BIGINT,
        'Rows loaded at the public update grain.' FROM raw_incident_update
    UNION ALL
    SELECT 'pricing_rows', 'info', count(*)::BIGINT,
        'GPU configurations parsed from the public pricing page.'
    FROM fact_instance_price_snapshot
    UNION ALL
    SELECT 'documented_region_rows', 'info', count(*)::BIGINT,
        'Regions parsed from the latest Lambda documentation snapshot.'
    FROM dim_region WHERE is_currently_documented
    UNION ALL
    SELECT 'github_repository_rows', 'info', count(*)::BIGINT,
        'Repositories in the latest public GitHub snapshot.'
    FROM mart_github_repository_latest
    UNION ALL
    SELECT 'github_event_rows', 'info', count(*)::BIGINT,
        'Deduplicated recent public events captured across snapshots.'
    FROM fact_github_event
    UNION ALL
    SELECT 'private_capacity_rows', 'info', count(*)::BIGINT,
        'Authenticated capacity observations loaded from untracked local snapshots.'
    FROM fact_instance_availability_snapshot
)
SELECT * FROM display_checks
UNION ALL
SELECT * FROM counts;
