CREATE TABLE fact_capacity_offering_snapshot AS
SELECT DISTINCT
    snapshot_at,
    offering_key,
    source_instance_type,
    gpu_description,
    gpu_count,
    vcpus,
    memory_gib,
    storage_gib,
    price_cents_per_hour,
    source_kind
FROM raw_capacity_offering;

CREATE TABLE fact_instance_availability_snapshot AS
SELECT DISTINCT
    availability.snapshot_at,
    availability.offering_key,
    availability.source_instance_type,
    region.region_id,
    availability.available,
    availability.source_kind
FROM raw_instance_availability AS availability
JOIN dim_region AS region USING (region_name);

CREATE TABLE mart_capacity_latest AS
WITH latest AS (
    SELECT max(snapshot_at) AS snapshot_at
    FROM fact_instance_availability_snapshot
)
SELECT
    availability.snapshot_at,
    availability.offering_key,
    availability.source_instance_type,
    offering.gpu_description,
    offering.gpu_count,
    offering.price_cents_per_hour,
    region.region_name,
    region.physical_location,
    availability.available,
    availability.source_kind
FROM fact_instance_availability_snapshot AS availability
JOIN latest USING (snapshot_at)
JOIN fact_capacity_offering_snapshot AS offering
    USING (snapshot_at, offering_key, source_instance_type, source_kind)
JOIN dim_region AS region USING (region_id);

CREATE TABLE mart_capacity_history AS
SELECT
    snapshot_at,
    source_kind,
    count(*) FILTER (WHERE available) AS available_offering_regions,
    count(DISTINCT region_id) FILTER (WHERE available) AS regions_with_capacity,
    count(DISTINCT source_instance_type) FILTER (WHERE available) AS offerings_with_capacity,
    count(*) AS evaluated_offering_regions
FROM fact_instance_availability_snapshot
GROUP BY snapshot_at, source_kind;
