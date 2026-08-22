CREATE TABLE mart_instance_catalog AS
SELECT
    snapshot_at,
    instance_type,
    gpu_model,
    gpu_count,
    vram_gb_per_gpu,
    gpu_count * vram_gb_per_gpu AS total_vram_gb,
    vcpus,
    ram_gib,
    storage_gib,
    price_per_gpu_hour,
    instance_price_per_hour,
    price_per_vram_gb_hour
FROM fact_instance_price_snapshot;

