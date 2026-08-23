# Five-minute interview walkthrough

## 0:00–0:30 — Problem

“I wanted to build something Lambda-specific that exercises the same reasoning as an internal data product. I used public reliability and GPU portfolio data, and I disclose that boundary throughout the app.”

## 0:30–1:00 — Users and sources

Primary users are Cloud Operations and Product; Data, Engineering, and Finance are secondary consumers. Show the independent source timestamps and coverage before any KPI.

## 1:00–2:00 — Model

Open the ER diagram. One row in `fact_incident` is one public incident. Explain that incident-to-region is many-to-many: a multi-region incident must not be duplicated in the fact or collapsed to one region. The same reasoning applies to derived themes. Point out that pricing, availability, and repositories have different snapshot grains rather than being timeless dimensions.

## 2:00–2:45 — Metrics

Define Public MTTR as first public update to public resolution. Explain why open incidents have null duration and why the app calls this “Public MTTR” rather than claiming Lambda's internal recovery time. Mention the latest-unambiguous severity rule and separate source impact field.

## 2:45–3:45 — Product

Use the overview for summary, then drill into one multi-region incident and show its timeline, theme evidence, and physical region metadata. Compare one GPU configuration, then show the live capacity heatmap and explicitly distinguish availability from inventory. Finish on the open-source view's owned/fork distinction and captured-event coverage.

## 3:45–4:20 — Trust

Open the quality panel. Show raw versus transformed counts, undocumented regions retained, open incidents retained, event overlap deduplicated, and build-blocking uniqueness/duration/arithmetic checks. Mention that the API key exists only in server configuration and capacity failures cannot prevent the public app from loading.

## 4:20–5:00 — Production tradeoff

“Public snapshots and DuckDB are appropriate for a fast, reproducible prototype. I would not propose scraping Lambda's public pages as its production reliability platform. Internally I would start from authoritative incident events and canonical service/region IDs, then add incremental ingestion, contracts, freshness monitoring, tested transformations, lineage, permissions, owners, and SLAs.”

## Likely follow-ups

- **Why `LEFT JOIN` in the explorer mart?** Incidents without an explicitly published region or derived theme must remain visible.
- **What happens with ten regions?** Ten bridge rows, one fact row, and `count(distinct incident_id)` in aggregates.
- **Why not estimate outage cost?** List price is not utilization, affected allocation, contracts, credits, or customer impact.
- **Why the latest severity?** Public updates may refine severity; the stored conflict count keeps those changes observable.
- **How is refresh safe?** Public sources validate before atomic promotion, incident/pricing remain paired, and deployment uses committed snapshots.
- **Why is capacity not in the public database?** It requires a credential and is operationally mutable. The public app keeps it in a short server cache; optional local history is explicitly private and gitignored.
- **Why no capacity trend online?** The deployment has no durable private store. A trend appears only after at least two local observations, avoiding synthetic history.
- **Are GitHub events complete?** No. They are a bounded recent public capture, deduplicated across snapshots and labeled accordingly.
- **Why no contributor rankings?** Event metadata cannot justify employee identity or productivity inference, and that metric would be inappropriate for this product.
- **What breaks first in production?** Presentation markup and free-text semantics, which is why the production path requires structured source contracts.
