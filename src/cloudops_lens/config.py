from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INCIDENT_DIR = RAW_DIR / "incidents"
PRICING_DIR = RAW_DIR / "pricing"
SQL_DIR = PROJECT_ROOT / "sql"
DEFAULT_DB_PATH = DATA_DIR / "cloudops_lens.duckdb"

INCIDENTS_URL = "https://status.lambda.ai/api/v2/incidents.json"
PRICING_URL = "https://lambda.ai/service/gpu-cloud"


@dataclass(frozen=True)
class SnapshotPaths:
    snapshot_id: str
    incidents: Path
    pricing: Path


def latest_snapshot() -> SnapshotPaths:
    """Return the latest timestamp for which both raw source files exist."""
    incident_ids = {path.stem for path in INCIDENT_DIR.glob("*.json")}
    pricing_ids = {path.stem for path in PRICING_DIR.glob("*.html")}
    common = sorted(incident_ids & pricing_ids)
    if not common:
        raise FileNotFoundError(
            "No complete source snapshot found. Run `uv run python -m cloudops_lens refresh`."
        )
    snapshot_id = common[-1]
    return SnapshotPaths(
        snapshot_id=snapshot_id,
        incidents=INCIDENT_DIR / f"{snapshot_id}.json",
        pricing=PRICING_DIR / f"{snapshot_id}.html",
    )
