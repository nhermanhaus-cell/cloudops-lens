CREATE TABLE dim_region AS
WITH latest_metadata AS (
    SELECT
        region_name,
        physical_location,
        country,
        geographic_group,
        snapshot_at AS metadata_snapshot_at
    FROM raw_region_metadata
    QUALIFY row_number() OVER (PARTITION BY region_name ORDER BY snapshot_at DESC) = 1
),
region_names AS (
    SELECT region_name FROM raw_incident_region
    UNION
    SELECT region_name FROM raw_region_metadata
    UNION
    SELECT region_name FROM raw_instance_availability
)
SELECT
    row_number() OVER (ORDER BY names.region_name) AS region_id,
    names.region_name,
    metadata.physical_location,
    metadata.country,
    metadata.geographic_group,
    metadata.region_name IS NOT NULL AS is_currently_documented,
    metadata.metadata_snapshot_at
FROM region_names AS names
LEFT JOIN latest_metadata AS metadata USING (region_name);

CREATE TABLE bridge_incident_region AS
SELECT DISTINCT
    source.incident_id,
    region.region_id,
    source.region_raw,
    source.normalization_status,
    source.evidence
FROM raw_incident_region AS source
JOIN dim_region AS region USING (region_name);
