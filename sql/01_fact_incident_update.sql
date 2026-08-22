CREATE TABLE fact_incident_update AS
SELECT
    incident_update_id,
    incident_id,
    lower(update_status) AS update_status,
    try_cast(created_at AS TIMESTAMPTZ) AS created_at,
    try_cast(display_at AS TIMESTAMPTZ) AS display_at,
    try_cast(updated_at AS TIMESTAMPTZ) AS updated_at,
    update_text,
    severity_extracted,
    snapshot_at
FROM raw_incident_update;

