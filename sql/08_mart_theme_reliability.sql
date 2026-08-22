CREATE TABLE mart_theme_reliability AS
SELECT
    theme.theme_slug,
    theme.theme_name,
    count(DISTINCT incident.incident_id) AS incident_count,
    median(incident.public_mttr_minutes) FILTER (WHERE incident.is_resolved)
        AS median_public_mttr_minutes
FROM dim_incident_theme AS theme
JOIN bridge_incident_theme AS bridge USING (theme_id)
JOIN fact_incident AS incident USING (incident_id)
GROUP BY theme.theme_slug, theme.theme_name;

