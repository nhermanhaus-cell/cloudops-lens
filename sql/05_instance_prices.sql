CREATE TABLE fact_instance_price_snapshot AS
SELECT
    snapshot_at,
    instance_type,
    gpu_model,
    gpu_count,
    vram_gb_per_gpu,
    vcpus,
    ram_gib,
    storage_gib,
    price_per_gpu_hour,
    instance_price_per_hour,
    price_per_vram_gb_hour
FROM raw_instance_price;

