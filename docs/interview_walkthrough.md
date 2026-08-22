# Five-minute interview walkthrough

## 0:00–0:30 — Problem

“I wanted to build something Lambda-specific that exercises the same reasoning as an internal data product. I used public reliability and GPU portfolio data, and I disclose that boundary throughout the app.”

## 0:30–1:00 — Users and sources

Primary users are Cloud Operations and Product; Data, Engineering, and Finance are secondary consumers. Show the snapshot timestamp and source coverage before any KPI.

## 1:00–2:00 — Model

Open the ER diagram. One row in `fact_incident` is one public incident. Explain that incident-to-region is many-to-many: a multi-region incident must not be duplicated in the fact or collapsed to one region. The same reasoning applies to derived themes. Point out that catalog price is a snapshot fact, not a mutable product dimension.

## 2:00–2:45 — Metrics

Define Public MTTR as first public update to public resolution. Explain why open incidents have null duration and why the app calls this “Public MTTR” rather than claiming Lambda's internal recovery time. Mention the latest-unambiguous severity rule and separate source impact field.

## 2:45–3:45 — Product

Use the overview for summary, then drill into one multi-region incident in the explorer and show the complete update history plus theme evidence. Finish with one GPU configuration comparison and the transparent workload arithmetic.

## 3:45–4:20 — Trust

Open the quality panel. Show raw versus transformed counts, missing regions retained as unknown, open incidents retained, alias corrections exposed, and build-blocking uniqueness/duration/arithmetic checks.

## 4:20–5:00 — Production tradeoff

“Public snapshots and DuckDB are appropriate for a fast, reproducible prototype. I would not propose scraping Lambda's public pages as its production reliability platform. Internally I would start from authoritative incident events and canonical service/region IDs, then add incremental ingestion, contracts, freshness monitoring, tested transformations, lineage, permissions, owners, and SLAs.”

## Likely follow-ups

- **Why `LEFT JOIN` in the explorer mart?** Incidents without an explicitly published region or derived theme must remain visible.
- **What happens with ten regions?** Ten bridge rows, one fact row, and `count(distinct incident_id)` in aggregates.
- **Why not estimate outage cost?** List price is not utilization, affected allocation, contracts, credits, or customer impact.
- **Why the latest severity?** Public updates may refine severity; the stored conflict count keeps those changes observable.
- **How is refresh safe?** Both sources validate before atomic promotion, and deployment uses the prior committed snapshot.
- **What breaks first in production?** Presentation markup and free-text semantics, which is why the production path requires structured source contracts.

