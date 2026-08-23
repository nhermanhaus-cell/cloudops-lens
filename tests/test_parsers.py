from __future__ import annotations

from cloudops_lens.config import latest_snapshot
from cloudops_lens.parsers import (
    classify_themes,
    extract_regions,
    extract_severity,
    github_event_category,
    latest_incident_severity,
    parse_capacity_gib,
    parse_pricing_html,
    parse_region_metadata_html,
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
    regions = extract_regions(
        "US-EAST-3, ap-southeaast-2, and europe-cental-1; not Dallas or AUS01"
    )
    assert [(row.raw, row.canonical) for row in regions] == [
        ("ap-southeaast-2", "ap-southeast-2"),
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


def test_region_metadata_parses_documented_location_and_geography() -> None:
    rows = parse_region_metadata_html(
        """
        <table><thead><tr><th>Region</th><th>Physical location</th></tr></thead>
        <tbody>
          <tr><td>US-EAST-1</td><td>Washington, D.C., USA</td></tr>
          <tr><td>europe-central-1</td><td>Frankfurt, Germany</td></tr>
        </tbody></table>
        """
    )
    assert rows[0].region_name == "us-east-1"
    assert rows[0].country == "USA"
    assert rows[0].geographic_group == "North America"
    assert rows[1].physical_location == "Frankfurt, Germany"


def test_region_metadata_rejects_changed_table_shape() -> None:
    import pytest

    with pytest.raises(ValueError, match="region table"):
        parse_region_metadata_html("<table><th>Zone</th><th>Location</th></table>")


def test_github_event_categories_do_not_infer_people_or_productivity() -> None:
    assert github_event_category("PushEvent") == "development"
    assert github_event_category("WatchEvent") == "ecosystem_engagement"
    assert github_event_category("MemberEvent") == "administration"


def test_committed_pricing_snapshot_has_expected_grain() -> None:
    rows = parse_pricing_html(latest_snapshot().pricing.read_text())
    assert {row.gpu_count for row in rows} == {1, 2, 4, 8}
    assert len(rows) >= 10
    assert len({row.instance_type for row in rows}) == len(rows)
    assert all(
        row.instance_price_per_hour == row.gpu_count * row.price_per_gpu_hour for row in rows
    )
