# Metric definitions

These contracts describe what the public data supports. They deliberately avoid internal operational claims.

## Incident lifecycle

### Incident

One distinct incident ID returned by Lambda's public status endpoint. The fact grain never changes when an incident mentions multiple regions or themes.

### Incident start

The earliest public update timestamp for the incident, preferring `display_at` and falling back to the update's `created_at`. This is a publication timestamp, not necessarily when customer impact or internal detection began.

### Resolution

The incident-level `resolved_at` timestamp when present, otherwise the earliest public update whose status is `resolved`. `resolution_source` records which rule supplied the value. Open incidents retain a null resolution.

### Public MTTR

`resolved_at - started_at`, expressed in minutes and calculated only for resolved incidents. Dashboard medians and P90 values exclude open incidents rather than treating them as zero.

The name is qualified because this measures the public communication window. It is not Lambda's internal mean time to detect, acknowledge, mitigate, recover, or restore service.

### Trailing 90 days

Incidents whose start is within 90 days of the selected snapshot timestamp. The calculation is anchored to the snapshot instead of the viewer's clock, making committed data reproducible.

### P90

DuckDB's continuous 90th percentile (`quantile_cont`) over non-null Public MTTR values in the selected window.

## Classification

### Severity

The latest chronological update containing exactly one value from `low`, `medium`, `high`, or `critical`. Brackets, Markdown, and capitalization are normalized. Lines containing template choices such as `[Low/Medium/High/Critical]` are ambiguous and ignored. If no valid line exists, severity is `unknown`.

The status API's separate `impact` value is retained as `source_impact`; it is not silently substituted for the published severity.

### Affected region

A region token explicitly present in the title or update text. Raw evidence is retained. Canonicalization lowercases and converts underscores to hyphens; documented aliases may correct a known spelling variant while preserving the source token. Facilities or city names are not mapped to regions unless a region token is also published.

### Derived incident theme

A deterministic, many-to-many analytical category triggered by a documented keyword in the incident title or update text. Current themes are networking, instance lifecycle, storage, power/facility, control plane, and managed services. The matching rule and evidence are stored on the bridge. Themes are derived annotations, not source-provided components.

## GPU catalog

### Catalog row

One distinct GPU model, VRAM-per-GPU value, and GPU-count configuration in a dated public pricing snapshot. VRAM is included in the key because the public catalog can list the same model and GPU count with multiple memory configurations.

### Instance hourly price

`gpu_count × price_per_gpu_hour`. This uses public list pricing and excludes taxes, discounts, commitments, credits, storage add-ons, and utilization.

### Price per GB of VRAM-hour

`price_per_gpu_hour ÷ vram_gb_per_gpu`. Because both numerator and denominator are per GPU, GPU count does not change this ratio.

