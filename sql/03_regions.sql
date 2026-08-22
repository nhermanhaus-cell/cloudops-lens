CREATE TABLE dim_region AS
SELECT
    row_number() OVER (ORDER BY region_name) AS region_id,
    region_name
FROM (SELECT DISTINCT region_name FROM raw_incident_region);

CREATE TABLE bridge_incident_region AS
SELECT DISTINCT
    source.incident_id,
    region.region_id,
    source.region_raw,
    source.normalization_status,
    source.evidence
FROM raw_incident_region AS source
JOIN dim_region AS region USING (region_name);

