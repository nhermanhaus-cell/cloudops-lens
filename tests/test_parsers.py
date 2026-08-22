from __future__ import annotations

from cloudops_lens.config import latest_snapshot
from cloudops_lens.parsers import (
    classify_themes,
    extract_regions,
    extract_severity,
    latest_incident_severity,
    parse_capacity_gib,
    parse_pricing_html,
)


def test_severity_requires_one_unambiguous_published_value() -> None:
    assert extract_severity("**Severity Level:** [High]") == "high"
    assert extract_severity("Severity: medium\nSummary: investigating") == "medium"
    assert extract_severity("Severity: [Low/Medium/High/Critical]") is None
    assert extract_severity("No severity was published") is None


def test_latest_incident_severity_skips_template_placeholder() -> None:
    updates = [
        {"created_at": "2026-01-01T00:00:00Z", "body": "Severity: Critical"},
        {
            "created_at": "2026-01-01T01:00:00Z",
            "body": "Severity: [Low/Medium/High/Critical]",
        },
        {"created_at": "2026-01-01T02:00:00Z", "body": "Severity: High"},
    ]
    assert latest_incident_severity(updates) == "high"


def test_regions_preserve_raw_values_and_document_aliases() -> None:
    regions = extract_regions("US-EAST-3 and europe-cental-1; not Dallas or AUS01")
    assert [(row.raw, row.canonical) for row in regions] == [
        ("europe-cental-1", "europe-central-1"),
        ("US-EAST-3", "us-east-3"),
    ]
    assert regions[0].normalization_status == "alias_corrected"


def test_theme_classification_is_many_to_many_and_evidenced() -> None:
    themes = classify_themes("A power event affected storage and network connectivity")
    assert {row.slug for row in themes} == {"networking", "power_facility", "storage"}
    assert all(row.rule_id.startswith("keyword:") and row.evidence for row in themes)


def test_capacity_units_normalize_to_gib() -> None:
    assert parse_capacity_gib("512 GiB SSD") == 512
    assert parse_capacity_gib("2.75 TiB SSD") == 2816


def test_committed_pricing_snapshot_has_expected_grain() -> None:
    rows = parse_pricing_html(latest_snapshot().pricing.read_text())
    assert {row.gpu_count for row in rows} == {1, 2, 4, 8}
    assert len(rows) >= 10
    assert len({row.instance_type for row in rows}) == len(rows)
    assert all(
        row.instance_price_per_hour == row.gpu_count * row.price_per_gpu_hour for row in rows
    )
