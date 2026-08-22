CREATE TABLE fact_incident AS
WITH update_rollup AS (
    SELECT
        incident_id,
        min(coalesce(display_at, created_at)) AS first_public_update_at,
        min(coalesce(display_at, created_at)) FILTER (WHERE update_status = 'resolved')
            AS first_resolved_update_at
    FROM fact_incident_update
    GROUP BY incident_id
),
latest_severity AS (
    SELECT incident_id, severity_extracted
    FROM fact_incident_update
    WHERE severity_extracted IS NOT NULL
    QUALIFY row_number() OVER (
        PARTITION BY incident_id
        ORDER BY coalesce(display_at, created_at) DESC, incident_update_id DESC
    ) = 1
),
severity_conflicts AS (
    SELECT incident_id, count(DISTINCT severity_extracted) AS published_severity_count
    FROM fact_incident_update
    WHERE severity_extracted IS NOT NULL
    GROUP BY incident_id
),
typed AS (
    SELECT
        source.incident_id,
        source.title,
        lower(source.status) AS status,
        lower(source.source_impact) AS source_impact,
        coalesce(severity.severity_extracted, 'unknown') AS severity,
        try_cast(source.created_at AS TIMESTAMPTZ) AS source_created_at,
        try_cast(source.updated_at AS TIMESTAMPTZ) AS source_updated_at,
        updates.first_public_update_at AS started_at,
        coalesce(
            try_cast(source.resolved_at AS TIMESTAMPTZ),
            updates.first_resolved_update_at
        ) AS resolved_at,
        CASE
            WHEN try_cast(source.resolved_at AS TIMESTAMPTZ) IS NOT NULL THEN 'incident.resolved_at'
            WHEN updates.first_resolved_update_at IS NOT NULL THEN 'resolved_update'
            ELSE NULL
        END AS resolution_source,
        coalesce(conflicts.published_severity_count, 0) AS published_severity_count,
        source.snapshot_at
    FROM raw_incident AS source
    LEFT JOIN update_rollup AS updates USING (incident_id)
    LEFT JOIN latest_severity AS severity USING (incident_id)
    LEFT JOIN severity_conflicts AS conflicts USING (incident_id)
)
SELECT
    *,
    CASE
        WHEN resolved_at IS NOT NULL AND started_at IS NOT NULL
        THEN date_diff('minute', started_at, resolved_at)
        ELSE NULL
    END AS public_mttr_minutes,
    resolved_at IS NOT NULL AS is_resolved
FROM typed;

