CREATE TABLE mart_incident_explorer AS
WITH regions AS (
    SELECT
        bridge.incident_id,
        string_agg(DISTINCT region.region_name, ', ' ORDER BY region.region_name) AS regions
    FROM bridge_incident_region AS bridge
    JOIN dim_region AS region USING (region_id)
    GROUP BY bridge.incident_id
),
themes AS (
    SELECT
        bridge.incident_id,
        string_agg(DISTINCT theme.theme_name, ', ' ORDER BY theme.theme_name) AS themes
    FROM bridge_incident_theme AS bridge
    JOIN dim_incident_theme AS theme USING (theme_id)
    GROUP BY bridge.incident_id
)
SELECT
    incident.incident_id,
    incident.title,
    incident.severity,
    incident.status,
    incident.started_at,
    incident.resolved_at,
    incident.public_mttr_minutes,
    coalesce(regions.regions, 'Unknown') AS regions,
    coalesce(themes.themes, 'Unclassified') AS themes,
    incident.snapshot_at
FROM fact_incident AS incident
LEFT JOIN regions USING (incident_id)
LEFT JOIN themes USING (incident_id);

