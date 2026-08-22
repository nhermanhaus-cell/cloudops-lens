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
