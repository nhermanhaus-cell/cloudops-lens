# Data model and grain

```mermaid
erDiagram
    FACT_INCIDENT ||--o{ FACT_INCIDENT_UPDATE : publishes
    FACT_INCIDENT ||--o{ BRIDGE_INCIDENT_REGION : affects
    DIM_REGION ||--o{ BRIDGE_INCIDENT_REGION : identifies
    FACT_INCIDENT ||--o{ BRIDGE_INCIDENT_THEME : classified_as
    DIM_INCIDENT_THEME ||--o{ BRIDGE_INCIDENT_THEME : identifies
    DIM_REGION ||--o{ FACT_INSTANCE_AVAILABILITY_SNAPSHOT : observed_in
    FACT_CAPACITY_OFFERING_SNAPSHOT ||--o{ FACT_INSTANCE_AVAILABILITY_SNAPSHOT : evaluated_as

    FACT_INCIDENT {
        string incident_id PK
        string severity
        timestamp started_at
        timestamp resolved_at
        integer public_mttr_minutes
    }
    FACT_INCIDENT_UPDATE {
        string incident_update_id PK
        string incident_id FK
        timestamp display_at
        string update_status
    }
    DIM_REGION {
        integer region_id PK
        string region_name
        string physical_location
        string country
        boolean is_currently_documented
    }
    BRIDGE_INCIDENT_REGION {
        string incident_id FK
        integer region_id FK
        string region_raw
        string evidence
    }
    DIM_INCIDENT_THEME {
        integer theme_id PK
        string theme_name
    }
    BRIDGE_INCIDENT_THEME {
        string incident_id FK
        integer theme_id FK
        string rule_id
        string evidence
    }
    FACT_INSTANCE_PRICE_SNAPSHOT {
        timestamp snapshot_at
        string instance_type
        integer gpu_count
        decimal price_per_gpu_hour
    }
    FACT_CAPACITY_OFFERING_SNAPSHOT {
        timestamp snapshot_at PK
        string offering_key PK
        string source_instance_type
        string gpu_description
    }
    FACT_INSTANCE_AVAILABILITY_SNAPSHOT {
        timestamp snapshot_at PK
        string offering_key PK
        integer region_id PK
        boolean available
    }
    FACT_GITHUB_REPOSITORY_SNAPSHOT {
        timestamp snapshot_at PK
        integer repository_id PK
        boolean is_fork
        boolean is_archived
        integer stargazers_count
    }
    FACT_GITHUB_EVENT {
        string event_id PK
        timestamp event_created_at
        string event_type
        string event_category
    }
```

## Why bridge tables

Incident-to-region and incident-to-theme are many-to-many relationships. Storing one region on the incident would lose published scope; duplicating the incident once per region would inflate headline incident and duration metrics. Bridge tables preserve both the incident grain and full scope. Analytical marts always use `count(distinct incident_id)` after bridge joins.

## Snapshot behavior

Raw source files share a timestamped snapshot ID. A snapshot becomes selectable only when both incident JSON and pricing HTML pass validation and are atomically written. The DuckDB file is generated and intentionally not committed.

Pricing and repository attributes are modeled as snapshot facts because both catalogs are mutable. GitHub event snapshots overlap and are loaded cumulatively, then deduplicated by stable event ID. Region metadata uses its latest independent snapshot and enriches incident regions without dropping codes that are no longer—or not yet—present in current documentation.

Authenticated capacity uses a separate security boundary. The deployed app requests current data into a 15-minute server-side cache without adding it to DuckDB. A developer may explicitly run `refresh-capacity` to create private, gitignored observations; `build` loads those only when present. Historical analysis therefore begins only after repeated private collection.

## Production evolution

The prototype's parsers intentionally expose evidence and quality state, but public presentation sources are not a production contract. A durable internal version should use authoritative incident and catalog services, stable identifiers, incremental or event-driven ingestion, schema contracts, freshness SLAs, ownership, lineage, access controls, and a governed semantic layer.
