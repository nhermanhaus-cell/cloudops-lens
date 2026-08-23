from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

VALID_SEVERITIES = ("low", "medium", "high", "critical")
SEVERITY_LINE_RE = re.compile(r"(?im)^.{0,8}severity(?:\s+level)?\s*[:*\]]+\s*(.+)$")
SEVERITY_VALUE_RE = re.compile(r"\b(low|medium|high|critical)\b", re.IGNORECASE)
REGION_RE = re.compile(
    r"\b(?:us|ap|asia|europe|me|australia|canada|southamerica)"
    r"[-_][a-z]+(?:[-_][a-z]+)*[-_]\d+\b",
    re.IGNORECASE,
)
CANONICAL_REGION_RE = re.compile(r"^[a-z]+(?:-[a-z]+)+-\d+$")

REGION_ALIASES = {
    "ap-southeaast-2": "ap-southeast-2",
    "europe-cental-1": "europe-central-1",
}

THEME_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "networking": (
        "Networking",
        ("network", "connectivity", "latency", "egress", "firewall", "infiniband", "dia circuit"),
    ),
    "instance_lifecycle": (
        "Instance lifecycle",
        ("launch", "boot", "termination", "instances failing", "instance in alert"),
    ),
    "storage": ("Storage", ("storage", "nfs", "vast", "filesystem", "file system")),
    "power_facility": (
        "Power / facility",
        ("power", "ups", "datacenter", "data center", "tornado", "maintenance"),
    ),
    "control_plane": (
        "Control plane",
        ("dashboard", "ruleset", "non-admin", "cloud website", "api"),
    ),
    "managed_services": (
        "Managed services",
        ("managed kubernetes", "1-click cluster", "1-click clusters"),
    ),
}


@dataclass(frozen=True)
class RegionMatch:
    raw: str
    canonical: str
    normalization_status: str


@dataclass(frozen=True)
class ThemeMatch:
    slug: str
    name: str
    rule_id: str
    evidence: str


@dataclass(frozen=True)
class RegionMetadata:
    region_name: str
    physical_location: str
    country: str
    geographic_group: str


@dataclass(frozen=True)
class PriceRow:
    instance_type: str
    gpu_model: str
    gpu_count: int
    vram_gb_per_gpu: float
    vcpus: int
    ram_gib: float
    storage_gib: float
    price_per_gpu_hour: float

    @property
    def instance_price_per_hour(self) -> float:
        return round(self.gpu_count * self.price_per_gpu_hour, 4)

    @property
    def price_per_vram_gb_hour(self) -> float:
        return round(self.price_per_gpu_hour / self.vram_gb_per_gpu, 6)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def extract_severity(text: str) -> str | None:
    """Return a severity only when one unambiguous value is published on a severity line."""
    values: set[str] = set()
    for line in SEVERITY_LINE_RE.findall(text or ""):
        values.update(value.lower() for value in SEVERITY_VALUE_RE.findall(line))
    return next(iter(values)) if len(values) == 1 else None


def latest_incident_severity(updates: list[dict[str, Any]]) -> str:
    ordered = sorted(
        updates,
        key=lambda row: row.get("display_at") or row.get("created_at") or "",
        reverse=True,
    )
    for update in ordered:
        severity = extract_severity(update.get("body", ""))
        if severity:
            return severity
    return "unknown"


def extract_regions(text: str) -> list[RegionMatch]:
    unique: dict[tuple[str, str], RegionMatch] = {}
    for match in REGION_RE.finditer(text or ""):
        raw = match.group(0)
        normalized = raw.lower().replace("_", "-")
        canonical = REGION_ALIASES.get(normalized, normalized)
        if canonical != normalized:
            status = "alias_corrected"
        elif CANONICAL_REGION_RE.fullmatch(canonical):
            status = "normalized"
        else:
            status = "unrecognized"
        unique[(raw, canonical)] = RegionMatch(raw, canonical, status)
    return sorted(unique.values(), key=lambda row: (row.canonical, row.raw))


def classify_themes(text: str) -> list[ThemeMatch]:
    lowered = (text or "").lower()
    matches: list[ThemeMatch] = []
    for slug, (name, keywords) in THEME_RULES.items():
        for keyword in keywords:
            if keyword in lowered:
                matches.append(
                    ThemeMatch(
                        slug=slug,
                        name=name,
                        rule_id=f"keyword:{keyword}",
                        evidence=keyword,
                    )
                )
                break
    return matches


def parse_region_metadata_html(html: str) -> list[RegionMetadata]:
    """Parse Lambda's documented region table without inventing missing geography."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[RegionMetadata] = []
    for table in soup.find_all("table"):
        headers = [cell.get_text(" ", strip=True) for cell in table.find_all("th")]
        if headers[:2] != ["Region", "Physical location"]:
            continue
        for table_row in table.select("tbody tr"):
            cells = [cell.get_text(" ", strip=True) for cell in table_row.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            region_name = cells[0].lower()
            physical_location = cells[1]
            country = physical_location.rsplit(",", maxsplit=1)[-1].strip()
            prefix = region_name.split("-", maxsplit=1)[0]
            geographic_group = {
                "us": "North America",
                "canada": "North America",
                "southamerica": "South America",
                "europe": "Europe",
                "me": "Middle East",
                "asia": "Asia Pacific",
                "ap": "Asia Pacific",
                "australia": "Asia Pacific",
            }.get(prefix, "Other")
            rows.append(
                RegionMetadata(
                    region_name=region_name,
                    physical_location=physical_location,
                    country=country,
                    geographic_group=geographic_group,
                )
            )
        break
    if not rows:
        raise ValueError("Lambda region table was not found")
    if len({row.region_name for row in rows}) != len(rows):
        raise ValueError("Lambda region table contains duplicate region codes")
    return rows


def github_event_category(event_type: str) -> str:
    if event_type in {"WatchEvent", "ForkEvent"}:
        return "ecosystem_engagement"
    if event_type in {"MemberEvent", "PublicEvent"}:
        return "administration"
    return "development"


def parse_number(value: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    if not match:
        raise ValueError(f"No numeric value found in {value!r}")
    return float(match.group(0))


def parse_capacity_gib(value: str) -> float:
    amount = parse_number(value)
    lowered = value.lower()
    if "tib" in lowered or " tb" in lowered:
        return amount * 1024
    if "gib" in lowered or " gb" in lowered:
        return amount
    raise ValueError(f"Unsupported capacity unit in {value!r}")


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def parse_pricing_html(html: str) -> list[PriceRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[PriceRow] = []
    buttons = soup.select('[role="tab"][aria-controls]')
    for button in buttons:
        label = button.get_text(" ", strip=True).lower()
        if not re.fullmatch(r"[1248]x", label):
            continue
        gpu_count = int(label.removesuffix("x"))
        panel = soup.find(id=button.get("aria-controls"))
        if panel is None:
            raise ValueError(f"Missing pricing panel for {label}")
        for table_row in panel.select("tbody tr"):
            plan_cell = table_row.find(attrs={"data-label": "Plan"})
            cells = {
                str(cell.get("data-label")): cell.get_text(" ", strip=True)
                for cell in table_row.find_all(attrs={"data-label": True})
            }
            required = {"VRAM/GPU", "vCPUs", "RAM", "STORAGE", "PRICE/GPU/HR*"}
            if plan_cell is None or not required.issubset(cells):
                raise ValueError(f"Pricing row changed shape: {cells}")
            model = plan_cell.get_text(" ", strip=True)
            vram_gb = parse_number(cells["VRAM/GPU"])
            vram_label = f"{vram_gb:g}gb"
            rows.append(
                PriceRow(
                    instance_type=f"{_slugify(model)}-{vram_label}-{gpu_count}x",
                    gpu_model=model,
                    gpu_count=gpu_count,
                    vram_gb_per_gpu=vram_gb,
                    vcpus=int(parse_number(cells["vCPUs"])),
                    ram_gib=parse_capacity_gib(cells["RAM"]),
                    storage_gib=parse_capacity_gib(cells["STORAGE"]),
                    price_per_gpu_hour=parse_number(cells["PRICE/GPU/HR*"]),
                )
            )
    counts = {row.gpu_count for row in rows}
    if counts != {1, 2, 4, 8}:
        raise ValueError(f"Expected pricing tabs for 1x, 2x, 4x, and 8x GPUs; found {counts}")
    if len(rows) < 10:
        raise ValueError(f"Expected at least 10 pricing rows; found {len(rows)}")
    if len({row.instance_type for row in rows}) != len(rows):
        raise ValueError("Pricing page produced duplicate instance types")
    return rows
