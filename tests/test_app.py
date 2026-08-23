from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_all_dashboard_views_render_without_exceptions() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run(timeout=90)
    assert not app.exception

    app.radio[0].set_value("Incident explorer").run(timeout=90)
    assert not app.exception
    assert app.selectbox

    app.radio[0].set_value("GPU product explorer").run(timeout=90)
    assert not app.exception
    assert len(app.selectbox) >= 1

    app.radio[0].set_value("Regional capacity").run(timeout=90)
    assert not app.exception
    assert any("unavailable" in warning.value.lower() for warning in app.warning)

    app.radio[0].set_value("Open source activity").run(timeout=90)
    assert not app.exception
