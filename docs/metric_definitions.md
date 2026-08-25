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

`resolved_at - started_at`, expressed in minutes and calculated only for resolved incidents. Dashboard means, medians, and P90 values exclude open incidents rather than treating them as zero.

### Mean time to public resolution

The arithmetic average Public MTTR across resolved incidents in the selected window. The dashboard spells out the term and retains the MTTR acronym for stakeholders who already recognize it.

The name is qualified because this measures the public communication window. It is not Lambda's internal mean time to detect, acknowledge, mitigate, recover, or restore service.

### Trailing 90 days

Incidents whose start is within 90 days of the selected snapshot timestamp. The calculation is anchored to the snapshot instead of the viewer's clock, making committed data reproducible.

### P90

DuckDB's continuous 90th percentile (`quantile_cont`) over non-null Public MTTR values in the selected window. Put plainly: 90% of resolved incidents reached public resolution within this duration, while the slowest 10% took longer.

## Classification

### Severity

The latest chronological update containing exactly one value from `low`, `medium`, `high`, or `critical`. Brackets, Markdown, and capitalization are normalized. Lines containing template choices such as `[Low/Medium/High/Critical]` are ambiguous and ignored. If no valid line exists, severity is `unknown`.

The status API's separate `impact` value is retained as `source_impact`; it is not silently substituted for the published severity.

### Affected region

A region token explicitly present in the title or update text. Raw evidence is retained. Canonicalization lowercases and converts underscores to hyphens; documented aliases may correct a known spelling variant while preserving the source token. Facilities or city names are not mapped to regions unless a region token is also published.

### Region metadata

Physical location, country, and geographic group parsed from the latest committed Lambda region-documentation snapshot. `is_currently_documented` indicates whether the canonical region appears in that snapshot. It is not interpreted as a region's historical opening or closure date.

### Derived incident theme

A deterministic, many-to-many analytical category triggered by a documented keyword in the incident title or update text. Current themes are networking, instance lifecycle, storage, power/facility, control plane, and managed services. The matching rule and evidence are stored on the bridge. Themes are derived annotations, not source-provided components.

## GPU catalog

### Catalog row

One distinct GPU model, VRAM-per-GPU value, and GPU-count configuration in a dated public pricing snapshot. VRAM is included in the key because the public catalog can list the same model and GPU count with multiple memory configurations.

### Instance hourly price

`gpu_count × price_per_gpu_hour`. This uses public list pricing and excludes taxes, discounts, commitments, credits, storage add-ons, and utilization.

### Price per GB of VRAM-hour

`price_per_gpu_hour ÷ vram_gb_per_gpu`. Because both numerator and denominator are per GPU, GPU count does not change this ratio.

## Regional capacity

### Availability observation

One authenticated API observation for one Lambda-native instance type and one observed region. The observed region universe is the union of `/regions` and every valid region reference in `regions_with_capacity_available`; this prevents endpoint disagreement from dropping a positive observation. The ingestion cross-joins retained GPU instance types and that region universe for comparison.

Each comparison row has one of two states:

- `reported_available`: the region appears in `regions_with_capacity_available` for the native instance type. This is a positive API observation.
- `not_reported_available`: the region does not appear in that positive list. This is a derived comparison state, not an explicit inventory-unavailable signal.

Existing private snapshots with the legacy `available` Boolean remain loadable and are interpreted using this same contract.

### Offering key

A normalized analytical key constructed from the API's GPU description—including model and VRAM—and GPU count. Lambda's source-native instance type is retained separately and remains part of the availability grain, so multiple native instance types may intentionally share one analytical offering key.

The Regional Capacity view is GPU-scoped. API catalog entries without a positive GPU count are counted and excluded as non-GPU offerings. Structurally incomplete entries are also counted and excluded when other valid GPU offerings remain; the source is unavailable only when no valid GPU offering survives normalization.

### Reported available type-region pair

One native instance-type/region row positively reported in the current response. Counts describe only the response timestamp. They are not inventory, capacity quantity, fleet size, utilization, guaranteed launchability, or an SLA. `price_cents_per_hour` is the whole native instance type's hourly price, not a per-GPU price.

Historical charts require at least two private local observations. Those files are stored only under gitignored `data/private/`; the public deployment does not create durable capacity history.

## Open-source activity

### Public repository

One repository returned by the LambdaLabsML organization REST endpoint at the repository snapshot timestamp. Owned repositories, forks, and archived repositories remain distinguishable.

### Active owned repository · 90 days

A non-fork, non-archived repository whose public `pushed_at` timestamp is within 90 days of the repository snapshot. This is a repository recency indicator, not a contributor or productivity metric.

### Stars on owned repositories

The sum of current `stargazers_count` across non-fork repositories in the latest snapshot. It is a mutable public counter, not historical stars gained during the captured event window.

### Recent captured public event

One unique GitHub organization event ID across all committed event snapshots. Overlapping captures are deduplicated by event ID. GitHub's organization-events endpoint is a bounded recent window, so counts are explicitly not complete activity history.

Events are grouped deterministically as development, ecosystem engagement, or administration based only on event type. No employee identity, contribution ranking, or productivity score is inferred.
