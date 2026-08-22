CREATE TABLE mart_region_reliability AS
SELECT
    region.region_name,
    count(DISTINCT incident.incident_id) AS incident_count,
    median(incident.public_mttr_minutes) FILTER (WHERE incident.is_resolved)
        AS median_public_mttr_minutes
FROM dim_region AS region
JOIN bridge_incident_region AS bridge USING (region_id)
JOIN fact_incident AS incident USING (incident_id)
GROUP BY region.region_name;

