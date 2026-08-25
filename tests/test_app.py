from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from cloudops_lens import capacity

LIVE_CAPACITY_FIXTURE = capacity.normalize_capacity_payloads(
    {
        "data": [
            {"name": "us-east-1", "description": "Washington, D.C."},
            {"name": "us-west-1", "description": "California"},
        ]
    },
    {
        "data": {
            "gpu_1x_a100": {
                "instance_type": {
                    "name": "gpu_1x_a100",
                    "gpu_description": "NVIDIA A100 40 GB",
                    "price_cents_per_hour": 129,
                    "specs": {
                        "gpus": 1,
                        "vcpus": 30,
                        "memory_gib": 200,
                        "storage_gib": 512,
                    },
                },
                "regions_with_capacity_available": [{"name": "us-east-1"}],
            }
        }
    },
)


def test_all_dashboard_views_render_without_exceptions() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run(timeout=90)
    assert not app.exception
    assert len(app.radio) == 2
    assert any(expander.label == "Other Lambda Data" for expander in app.expander)

    app.radio[0].set_value("Incident explorer").run(timeout=90)
    assert not app.exception
    assert len(app.button) >= 1
    app.button[0].click().run(timeout=90)
    assert not app.exception
    assert any("Published update timeline" in markdown.value for markdown in app.markdown)
    assert app.button[0].label.startswith("▼")

    app.radio[1].set_value("GPU product explorer").run(timeout=90)
    assert not app.exception
    assert len(app.selectbox) >= 1

    app.radio[0].set_value("Regional capacity").run(timeout=90)
    assert not app.exception
    assert any("unavailable" in warning.value.lower() for warning in app.warning)

    app.radio[1].set_value("Open source activity").run(timeout=90)
    assert not app.exception


def test_live_capacity_view_uses_qualified_source_language(monkeypatch) -> None:
    monkeypatch.setenv("LAMBDA_API_KEY", "test-placeholder")
    monkeypatch.setattr(capacity, "fetch_capacity_snapshot", lambda _key: LIVE_CAPACITY_FIXTURE)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run(timeout=90)
    app.radio[0].set_value("Regional capacity").run(timeout=90)

    assert not app.exception
    metric_labels = {metric.label for metric in app.metric}
    assert "Reported available type-region pairs" in metric_labels
    assert "Regions with reported availability" in metric_labels
    assert "GPU instance types with reported availability" in metric_labels
    rendered_text = " ".join(
        element.value
        for collection in (app.caption, app.info, app.markdown)
        for element in collection
    )
    assert "Not reported available" in rendered_text
    assert "not describe inventory quantity" in rendered_text
    assert "Only **positive API assignments** are source-reported" in rendered_text
    capacity_table = app.dataframe[0].value
    assert set(capacity_table["Status"]) == {
        "Reported available",
        "Not reported available",
    }
    assert "Whole-instance hourly price" in capacity_table.columns
