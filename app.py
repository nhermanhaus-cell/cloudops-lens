from __future__ import annotations

import os

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

from cloudops_lens.capacity import CapacityUnavailable, fetch_capacity_snapshot
from cloudops_lens.config import DEFAULT_DB_PATH, PROJECT_ROOT
from cloudops_lens.pipeline import build_database

st.set_page_config(
    page_title="CloudOps Lens",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)

COLORS = {
    "critical": "#ef4444",
    "high": "#f97316",
    "medium": "#f59e0b",
    "low": "#38bdf8",
    "unknown": "#94a3b8",
}


@st.cache_resource(show_spinner="Building the analytical model from the committed snapshot…")
def database_path() -> str:
    if DEFAULT_DB_PATH.exists():
        return str(DEFAULT_DB_PATH)
    cache_path = PROJECT_ROOT / ".cache" / "dashboard.duckdb"
    build_database(cache_path)
    return str(cache_path)


@st.cache_data(show_spinner=False)
def query(sql: str, parameters: tuple = ()) -> pd.DataFrame:
    with duckdb.connect(database_path(), read_only=True) as connection:
        return connection.execute(sql, parameters).fetchdf()


def lambda_api_key() -> str | None:
    """Resolve a server-side credential without exposing it to a widget or log."""
    environment_key = os.getenv("LAMBDA_API_KEY")
    if environment_key:
        return environment_key
    try:
        return st.secrets.get("LAMBDA_API_KEY")
    except (FileNotFoundError, KeyError):
        return None


@st.cache_data(ttl=900, show_spinner="Checking current regional capacity…")
def cached_live_capacity(_api_key: str) -> dict:
    return fetch_capacity_snapshot(_api_key)


def metric_duration(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    minutes = int(round(float(value)))
    if minutes < 60:
        return f"{minutes}m"
    hours, remaining = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {remaining}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def styled_figure(figure, height: int = 330):
    figure.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=42, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, ui-sans-serif, system-ui", color="#cbd5e1"),
        title_font=dict(size=16, color="#f8fafc"),
        legend_title_text="",
        hoverlabel=dict(bgcolor="#111827", font_color="#f8fafc"),
    )
    figure.update_xaxes(gridcolor="rgba(148,163,184,.12)", zeroline=False)
    figure.update_yaxes(gridcolor="rgba(148,163,184,.12)", zeroline=False)
    return figure


def quality_panel() -> None:
    quality = query(
        """
        SELECT check_name, status, record_count, detail
        FROM mart_data_quality
        ORDER BY
            CASE status WHEN 'fail' THEN 1 WHEN 'warn' THEN 2 WHEN 'pass' THEN 3 ELSE 4 END,
            check_name
        """
    )
    with st.expander("Data quality & lineage", expanded=False):
        status_icons = {"pass": "✓", "warn": "!", "fail": "×", "info": "i"}
        shown = quality.copy()
        shown["Status"] = (
            shown["status"].map(status_icons).fillna("·") + " " + shown["status"].str.upper()
        )
        shown["Check"] = shown["check_name"].str.replace("_", " ").str.title()
        shown["Records"] = shown["record_count"].astype(int)
        shown["Meaning"] = shown["detail"]
        st.dataframe(
            shown[["Status", "Check", "Records", "Meaning"]],
            hide_index=True,
            use_container_width=True,
            column_config={"Meaning": st.column_config.TextColumn(width="large")},
        )
        st.caption(
            "Warnings expose public-source limitations; they do not silently remove records. "
            "Build-blocking checks cover duplicate IDs, negative durations, orphan bridges, "
            "and pricing arithmetic."
        )


def overview() -> None:
    summary = query("SELECT * FROM mart_reliability_overview").iloc[0]
    incidents = query(
        """
        SELECT * FROM fact_incident
        WHERE started_at >= (SELECT max(snapshot_at) - INTERVAL 90 DAY FROM raw_snapshot_metadata)
          AND started_at <= (SELECT max(snapshot_at) FROM raw_snapshot_metadata)
        ORDER BY started_at
        """
    )
    coverage = query(
        """
        SELECT min(started_at) AS coverage_start, max(started_at) AS coverage_end,
               max(snapshot_at) AS snapshot_at, count(*) AS incident_count
        FROM fact_incident
        """
    ).iloc[0]

    st.markdown("## Reliability overview")
    st.caption(
        f"Available public history: {coverage.coverage_start:%b %d, %Y}–"
        f"{coverage.coverage_end:%b %d, %Y} · {int(coverage.incident_count)} incidents · "
        f"snapshot {coverage.snapshot_at:%b %d, %Y %H:%M UTC}"
    )
    columns = st.columns(4)
    columns[0].metric("Incidents · 90d", int(summary.incidents_90d))
    columns[1].metric("High / critical", int(summary.high_critical_incidents_90d))
    columns[2].metric("Median Public MTTR", metric_duration(summary.median_public_mttr_minutes))
    columns[3].metric("P90 Public MTTR", metric_duration(summary.p90_public_mttr_minutes))

    left, right = st.columns((1.55, 1))
    with left:
        week = incidents["started_at"].dt.tz_convert(None).dt.to_period("W").dt.start_time
        trend = incidents.assign(week=week)
        trend = trend.groupby("week", as_index=False).agg(incidents=("incident_id", "nunique"))
        figure = px.bar(trend, x="week", y="incidents", title="Incidents by week")
        figure.update_traces(
            marker_color="#7c3aed", hovertemplate="%{x|%b %d}<br>%{y} incidents<extra></extra>"
        )
        st.plotly_chart(styled_figure(figure), use_container_width=True)
    with right:
        severity = (
            incidents.groupby("severity", as_index=False)
            .agg(incidents=("incident_id", "nunique"))
            .sort_values("incidents")
        )
        figure = px.bar(
            severity,
            x="incidents",
            y="severity",
            orientation="h",
            title="Severity mix",
            color="severity",
            color_discrete_map=COLORS,
        )
        figure.update_traces(hovertemplate="%{y}<br>%{x} incidents<extra></extra>")
        st.plotly_chart(styled_figure(figure), use_container_width=True)

    left, middle, right = st.columns(3)
    with left:
        region = query(
            """
            SELECT * FROM mart_region_reliability
            ORDER BY incident_count DESC, region_name LIMIT 10
            """
        ).sort_values("incident_count")
        figure = px.bar(
            region,
            x="incident_count",
            y="region_name",
            orientation="h",
            title="Affected regions",
            hover_data={"physical_location": True, "incident_count": False},
        )
        figure.update_traces(
            marker_color="#38bdf8", hovertemplate="%{y}<br>%{x} incidents<extra></extra>"
        )
        st.plotly_chart(styled_figure(figure, 360), use_container_width=True)
    with middle:
        themes = query(
            "SELECT * FROM mart_theme_reliability ORDER BY incident_count DESC, theme_name"
        ).sort_values("incident_count")
        figure = px.bar(
            themes,
            x="incident_count",
            y="theme_name",
            orientation="h",
            title="Derived incident themes",
        )
        figure.update_traces(
            marker_color="#a78bfa", hovertemplate="%{y}<br>%{x} incidents<extra></extra>"
        )
        st.plotly_chart(styled_figure(figure, 360), use_container_width=True)
    with right:
        resolved = incidents[incidents["public_mttr_minutes"].notna()].copy()
        resolved["Public MTTR (hours)"] = resolved["public_mttr_minutes"] / 60
        figure = px.histogram(
            resolved,
            x="Public MTTR (hours)",
            nbins=12,
            title="Public MTTR distribution",
        )
        figure.update_traces(
            marker_color="#f59e0b", hovertemplate="%{x:.1f} hours<br>%{y} incidents<extra></extra>"
        )
        st.plotly_chart(styled_figure(figure, 360), use_container_width=True)

    st.info(
        "**Interpretation guardrail:** Public MTTR measures elapsed time between the first "
        "published update and public resolution. It is not Lambda's internal detection, "
        "mitigation, or service-restoration time."
    )
    quality_panel()


def incident_explorer() -> None:
    data = query("SELECT * FROM mart_incident_explorer ORDER BY started_at DESC")
    st.markdown("## Incident explorer")
    st.caption("Filter the summary, then inspect the exact public update history behind any row.")

    min_date = data["started_at"].min().date()
    max_date = data["started_at"].max().date()
    filter_columns = st.columns((1.3, 1, 1, 1, 1))
    date_range = filter_columns[0].date_input(
        "Published date",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    severities = filter_columns[1].multiselect(
        "Severity", sorted(data["severity"].unique()), placeholder="All"
    )
    statuses = filter_columns[2].multiselect(
        "Status", sorted(data["status"].unique()), placeholder="All"
    )
    all_regions = sorted(
        {part.strip() for value in data["regions"] for part in str(value).split(",")}
    )
    regions = filter_columns[3].multiselect("Region", all_regions, placeholder="All")
    all_themes = sorted(
        {part.strip() for value in data["themes"] for part in str(value).split(",")}
    )
    themes = filter_columns[4].multiselect("Theme", all_themes, placeholder="All")

    filtered = data.copy()
    if isinstance(date_range, tuple) and len(date_range) == 2:
        filtered = filtered[filtered["started_at"].dt.date.between(date_range[0], date_range[1])]
    if severities:
        filtered = filtered[filtered["severity"].isin(severities)]
    if statuses:
        filtered = filtered[filtered["status"].isin(statuses)]
    if regions:
        filtered = filtered[
            filtered["regions"].apply(
                lambda value: bool(set(regions) & {part.strip() for part in value.split(",")})
            )
        ]
    if themes:
        filtered = filtered[
            filtered["themes"].apply(
                lambda value: bool(set(themes) & {part.strip() for part in value.split(",")})
            )
        ]

    shown = filtered.copy()
    shown["Public MTTR"] = shown["public_mttr_minutes"].map(metric_duration)
    shown["Started"] = shown["started_at"].dt.strftime("%b %d, %Y %H:%M UTC")
    shown["Resolved"] = shown["resolved_at"].dt.strftime("%b %d, %Y %H:%M UTC").fillna("Open")
    shown["Severity"] = shown["severity"].str.title()
    shown["Status"] = shown["status"].str.title()
    st.markdown(f"**{len(shown)} incidents**")
    st.dataframe(
        shown[
            [
                "title",
                "Severity",
                "Status",
                "regions",
                "themes",
                "Started",
                "Resolved",
                "Public MTTR",
            ]
        ].rename(columns={"title": "Incident", "regions": "Regions", "themes": "Derived themes"}),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Incident": st.column_config.TextColumn(width="large"),
            "Derived themes": st.column_config.TextColumn(width="medium"),
        },
    )

    if filtered.empty:
        st.warning("No incidents match the selected filters.")
        quality_panel()
        return
    labels = {
        row.incident_id: f"{row.started_at:%Y-%m-%d} · {row.title}" for row in filtered.itertuples()
    }
    selected_id = st.selectbox(
        "Inspect incident timeline",
        options=list(labels),
        format_func=lambda value: labels[value],
    )
    selected = filtered.loc[filtered["incident_id"] == selected_id].iloc[0]
    detail_columns = st.columns(4)
    detail_columns[0].metric("Severity", selected.severity.title())
    detail_columns[1].metric("Status", selected.status.title())
    detail_columns[2].metric("Public MTTR", metric_duration(selected.public_mttr_minutes))
    detail_columns[3].metric(
        "Regions", len(selected.regions.split(",")) if selected.regions != "Unknown" else 0
    )

    evidence = query(
        """
        SELECT theme.theme_name, bridge.rule_id, bridge.evidence
        FROM bridge_incident_theme AS bridge
        JOIN dim_incident_theme AS theme USING (theme_id)
        WHERE bridge.incident_id = ?
        ORDER BY theme.theme_name
        """,
        (selected_id,),
    )
    if not evidence.empty:
        st.caption(
            "Derived themes: "
            + " · ".join(f"{row.theme_name} (`{row.rule_id}`)" for row in evidence.itertuples())
        )
    region_detail = query(
        """
        SELECT region.region_name, region.physical_location, region.country,
               region.is_currently_documented
        FROM bridge_incident_region AS bridge
        JOIN dim_region AS region USING (region_id)
        WHERE bridge.incident_id = ?
        ORDER BY region.region_name
        """,
        (selected_id,),
    )
    if not region_detail.empty:
        descriptions = []
        for row in region_detail.itertuples():
            location = row.physical_location or "location not in current region documentation"
            descriptions.append(f"{row.region_name} — {location}")
        st.caption("Region metadata: " + " · ".join(descriptions))
    timeline = query(
        """
        SELECT update_status, coalesce(display_at, created_at) AS published_at, update_text
        FROM fact_incident_update
        WHERE incident_id = ?
        ORDER BY coalesce(display_at, created_at), incident_update_id
        """,
        (selected_id,),
    )
    for update in timeline.itertuples():
        with st.container(border=True):
            st.markdown(
                f"**{str(update.update_status).title()}** · "
                f"{update.published_at:%b %d, %Y %H:%M UTC}"
            )
            st.markdown(update.update_text)
    quality_panel()


def gpu_explorer() -> None:
    catalog = query("SELECT * FROM mart_instance_catalog ORDER BY gpu_count, gpu_model")
    st.markdown("## GPU product explorer")
    st.caption(
        f"Public catalog snapshot: {catalog.snapshot_at.max():%b %d, %Y %H:%M UTC} · "
        f"{len(catalog)} configurations"
    )
    filter_a, filter_b = st.columns((1.5, 1))
    models = filter_a.multiselect(
        "GPU model", sorted(catalog["gpu_model"].unique()), placeholder="All models"
    )
    counts = filter_b.multiselect(
        "GPU count", sorted(catalog["gpu_count"].unique()), placeholder="All sizes"
    )
    filtered = catalog.copy()
    if models:
        filtered = filtered[filtered["gpu_model"].isin(models)]
    if counts:
        filtered = filtered[filtered["gpu_count"].isin(counts)]

    shown = filtered.copy()
    shown["Price / GPU-hour"] = shown["price_per_gpu_hour"].map(lambda value: f"${value:,.2f}")
    shown["Instance / hour"] = shown["instance_price_per_hour"].map(lambda value: f"${value:,.2f}")
    shown["Storage GiB"] = shown["storage_gib"].map(lambda value: f"{value:,.0f}")
    st.dataframe(
        shown[
            [
                "gpu_model",
                "gpu_count",
                "vram_gb_per_gpu",
                "vcpus",
                "ram_gib",
                "Storage GiB",
                "Price / GPU-hour",
                "Instance / hour",
            ]
        ].rename(
            columns={
                "gpu_model": "GPU",
                "gpu_count": "GPUs",
                "vram_gb_per_gpu": "VRAM / GPU (GB)",
                "vcpus": "vCPUs",
                "ram_gib": "RAM (GiB)",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )

    left, right = st.columns(2)
    with left:
        figure = px.scatter(
            filtered,
            x="total_vram_gb",
            y="instance_price_per_hour",
            size="gpu_count",
            color="gpu_model",
            hover_name="instance_type",
            title="Instance price vs. total VRAM",
            labels={
                "total_vram_gb": "Total VRAM (GB)",
                "instance_price_per_hour": "Instance price / hour ($)",
                "gpu_model": "GPU",
            },
        )
        st.plotly_chart(styled_figure(figure, 390), use_container_width=True)
    with right:
        efficiency = filtered.sort_values("price_per_vram_gb_hour", ascending=False)
        figure = px.bar(
            efficiency,
            x="price_per_vram_gb_hour",
            y="instance_type",
            orientation="h",
            color="gpu_model",
            title="Cost per GB of VRAM-hour",
            labels={
                "price_per_vram_gb_hour": "$ / GB VRAM-hour",
                "instance_type": "Configuration",
                "gpu_model": "GPU",
            },
        )
        st.plotly_chart(styled_figure(figure, 390), use_container_width=True)

    st.markdown("### Workload cost calculator")
    calculator_a, calculator_b, calculator_c = st.columns((2, 1, 1.2))
    options = filtered["instance_type"].tolist() or catalog["instance_type"].tolist()
    selected_type = calculator_a.selectbox("Configuration", options)
    hours = calculator_b.number_input("Hours", min_value=0.5, max_value=720.0, value=12.0, step=0.5)
    selected = catalog.loc[catalog["instance_type"] == selected_type].iloc[0]
    cost = selected.instance_price_per_hour * hours
    calculator_c.metric("Estimated compute cost", f"${cost:,.2f}")
    st.caption(
        f"{selected.gpu_count} × {selected.gpu_model} × "
        f"${selected.price_per_gpu_hour:.2f}/GPU-hour × "
        f"{hours:g} hours. Public list price only; plus applicable sales tax/VAT/GST."
    )
    quality_panel()


def regional_capacity() -> None:
    st.markdown("## Regional capacity")
    st.caption(
        "Current launch availability returned by Lambda's authenticated Cloud API. "
        "The response is cached server-side for 15 minutes and is never committed to this "
        "repository."
    )
    api_key = lambda_api_key()
    if not api_key:
        st.warning(
            "Live capacity is unavailable because `LAMBDA_API_KEY` is not configured on this "
            "server. The other four views remain fully functional from public snapshots."
        )
        st.info(
            "Configure the key as an environment variable locally or in Streamlit Community "
            "Cloud's encrypted secrets. CloudOps Lens never accepts credentials through the UI."
        )
        _capacity_history()
        quality_panel()
        return

    try:
        payload = cached_live_capacity(api_key)
    except CapacityUnavailable as error:
        st.warning(f"Live capacity is temporarily unavailable: {error}")
        _capacity_history()
        quality_panel()
        return

    offerings = pd.DataFrame(payload["offerings"])
    availability = pd.DataFrame(payload["availability"])
    current = availability.merge(
        offerings,
        on=["offering_key", "source_instance_type"],
        how="left",
        validate="many_to_one",
    )
    region_metadata = query(
        """
        SELECT region_name, physical_location, country, geographic_group
        FROM dim_region
        """
    )
    current = current.merge(region_metadata, on="region_name", how="left", validate="many_to_one")
    current["offering_label"] = current["source_instance_type"] + " · " + current["gpu_description"]
    snapshot_at = pd.Timestamp(payload["snapshot_at"])
    age_minutes = max(0, int((pd.Timestamp.now(tz="UTC") - snapshot_at).total_seconds() / 60))
    available = current[current["available"]]
    cards = st.columns(4)
    cards[0].metric("Available pairs", len(available))
    cards[1].metric("Regions with capacity", available["region_name"].nunique())
    cards[2].metric("Available instance types", available["source_instance_type"].nunique())
    cards[3].metric("Cache age", f"{age_minutes}m")
    st.caption(
        f"API observation: {snapshot_at:%b %d, %Y %H:%M UTC} · "
        f"{len(offerings)} offerings evaluated across {availability.region_name.nunique()} regions"
    )

    matrix = current.pivot_table(
        index="offering_label",
        columns="region_name",
        values="available",
        aggfunc="max",
    ).astype(int)
    heatmap = px.imshow(
        matrix,
        color_continuous_scale=[[0, "#172033"], [1, "#22c55e"]],
        zmin=0,
        zmax=1,
        aspect="auto",
        title="GPU offering availability by region",
        labels={"x": "Region", "y": "GPU offering", "color": "Available"},
    )
    heatmap.update_coloraxes(showscale=False)
    st.plotly_chart(styled_figure(heatmap, max(390, 38 * len(matrix))), use_container_width=True)

    left, right = st.columns(2)
    by_region = (
        available.groupby(["region_name", "physical_location"], as_index=False, dropna=False)
        .agg(available_offerings=("source_instance_type", "nunique"))
        .sort_values("available_offerings")
    )
    by_gpu = (
        available.groupby("gpu_description", as_index=False)
        .agg(regional_breadth=("region_name", "nunique"))
        .sort_values("regional_breadth")
    )
    with left:
        figure = px.bar(
            by_region,
            x="available_offerings",
            y="region_name",
            orientation="h",
            title="Available offerings by region",
            hover_data={"physical_location": True, "available_offerings": False},
        )
        figure.update_traces(marker_color="#38bdf8")
        st.plotly_chart(styled_figure(figure), use_container_width=True)
    with right:
        figure = px.bar(
            by_gpu,
            x="regional_breadth",
            y="gpu_description",
            orientation="h",
            title="Regional breadth by GPU",
        )
        figure.update_traces(marker_color="#a78bfa")
        st.plotly_chart(styled_figure(figure), use_container_width=True)

    shown = current.copy()
    shown["Status"] = shown["available"].map({True: "Available", False: "Unavailable"})
    shown["Hourly price"] = shown["price_cents_per_hour"].map(lambda value: f"${value / 100:,.2f}")
    st.dataframe(
        shown[
            [
                "source_instance_type",
                "gpu_description",
                "gpu_count",
                "region_name",
                "physical_location",
                "Status",
                "Hourly price",
            ]
        ].rename(
            columns={
                "source_instance_type": "Instance type",
                "gpu_description": "GPU",
                "gpu_count": "GPUs",
                "region_name": "Region",
                "physical_location": "Physical location",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )
    _capacity_history()
    st.info(
        "**Interpretation guardrail:** Availability is an API observation, not inventory, "
        "fleet size, utilization, guaranteed launchability, or an SLA."
    )
    quality_panel()


def _capacity_history() -> None:
    history = query("SELECT * FROM mart_capacity_history ORDER BY snapshot_at")
    if history["snapshot_at"].nunique() < 2:
        st.info(
            "Capacity history begins after repeated local collection. Run `refresh-capacity` "
            "at different times to accumulate private, gitignored observations."
        )
        return
    figure = px.line(
        history,
        x="snapshot_at",
        y="available_offering_regions",
        markers=True,
        title="Private local availability history",
        labels={"snapshot_at": "Observation", "available_offering_regions": "Available pairs"},
    )
    st.plotly_chart(styled_figure(figure), use_container_width=True)


def open_source_activity() -> None:
    st.markdown("## Open source activity")
    st.caption(
        "LambdaLabsML public repository portfolio and a bounded, recent capture of public "
        "organization events. This is not complete activity history."
    )
    repositories = query(
        "SELECT * FROM mart_github_repository_latest ORDER BY stargazers_count DESC"
    )
    events = query("SELECT * FROM fact_github_event ORDER BY event_created_at")
    if repositories.empty:
        st.warning("No committed GitHub snapshot is available. Run the public `refresh` command.")
        quality_panel()
        return
    portfolio = query("SELECT * FROM mart_github_portfolio").iloc[0]
    cards = st.columns(4)
    cards[0].metric("Public repositories", int(portfolio.public_repositories))
    cards[1].metric("Active owned · 90d", int(portfolio.active_owned_repositories_90d))
    cards[2].metric("Stars · owned repos", int(portfolio.stars_on_owned_repositories or 0))
    cards[3].metric("Captured events", len(events))
    if not events.empty:
        st.caption(
            f"Repository snapshot: {portfolio.snapshot_at:%b %d, %Y %H:%M UTC} · "
            f"captured event window: {events.event_created_at.min():%b %d, %Y}–"
            f"{events.event_created_at.max():%b %d, %Y}"
        )

    left, right = st.columns((1.45, 1))
    with left:
        activity = query("SELECT * FROM mart_github_activity_daily ORDER BY event_date")
        if not activity.empty:
            figure = px.bar(
                activity,
                x="event_date",
                y="event_count",
                color="event_category",
                title="Recent captured public events",
                labels={
                    "event_date": "Date",
                    "event_count": "Events",
                    "event_category": "Category",
                },
            )
            st.plotly_chart(styled_figure(figure), use_container_width=True)
    with right:
        language = (
            repositories[(~repositories["is_fork"]) & repositories["language"].notna()]
            .groupby("language", as_index=False)
            .agg(repositories=("repository_id", "nunique"))
            .sort_values("repositories", ascending=False)
        )
        figure = px.pie(
            language.head(10),
            values="repositories",
            names="language",
            hole=0.55,
            title="Owned repository language mix",
        )
        st.plotly_chart(styled_figure(figure), use_container_width=True)

    left, right = st.columns(2)
    with left:
        event_types = (
            events.groupby(["event_category", "event_type"], as_index=False)
            .agg(events=("event_id", "nunique"))
            .sort_values("events")
        )
        if not event_types.empty:
            figure = px.bar(
                event_types,
                x="events",
                y="event_type",
                color="event_category",
                orientation="h",
                title="Captured event types",
            )
            st.plotly_chart(styled_figure(figure, 390), use_container_width=True)
    with right:
        top = (
            repositories[~repositories["is_fork"]]
            .nlargest(12, "stargazers_count")
            .sort_values("stargazers_count")
        )
        figure = px.bar(
            top,
            x="stargazers_count",
            y="name",
            orientation="h",
            title="Top owned repositories by stars",
        )
        figure.update_traces(marker_color="#f59e0b")
        st.plotly_chart(styled_figure(figure, 390), use_container_width=True)

    filter_columns = st.columns(3)
    ownership = filter_columns[0].selectbox("Ownership", ["All", "Owned", "Fork"])
    archive = filter_columns[1].selectbox("Lifecycle", ["All", "Active", "Archived"])
    languages = filter_columns[2].multiselect(
        "Language", sorted(repositories["language"].dropna().unique()), placeholder="All"
    )
    catalog = repositories.copy()
    if ownership == "Owned":
        catalog = catalog[~catalog["is_fork"]]
    elif ownership == "Fork":
        catalog = catalog[catalog["is_fork"]]
    if archive == "Active":
        catalog = catalog[~catalog["is_archived"]]
    elif archive == "Archived":
        catalog = catalog[catalog["is_archived"]]
    if languages:
        catalog = catalog[catalog["language"].isin(languages)]
    shown = catalog.copy()
    shown["Ownership"] = shown["is_fork"].map({False: "Owned", True: "Fork"})
    shown["Lifecycle"] = shown["is_archived"].map({False: "Active", True: "Archived"})
    st.dataframe(
        shown[
            [
                "name",
                "Ownership",
                "Lifecycle",
                "language",
                "stargazers_count",
                "forks_count",
                "pushed_at",
                "html_url",
            ]
        ].rename(
            columns={
                "name": "Repository",
                "language": "Language",
                "stargazers_count": "Stars",
                "forks_count": "Forks",
                "pushed_at": "Last push",
                "html_url": "GitHub",
            }
        ),
        hide_index=True,
        use_container_width=True,
        column_config={"GitHub": st.column_config.LinkColumn()},
    )
    st.info(
        "Event categories describe public event types only. They do not identify employees or "
        "measure individual or team productivity."
    )
    quality_panel()


st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] { background: #070b14; }
    [data-testid="stSidebar"] { background: #0c1220; border-right: 1px solid #1e293b; }
    [data-testid="stMetric"] {
        background: linear-gradient(145deg, rgba(30,41,59,.72), rgba(15,23,42,.82));
        border: 1px solid #243147; border-radius: 14px; padding: 16px 18px;
    }
    [data-testid="stMetricValue"] { color: #f8fafc; }
    [data-testid="stDataFrame"] {
        border: 1px solid #1e293b; border-radius: 12px; overflow: hidden;
    }
    .block-container { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1500px; }
    h1, h2, h3 { letter-spacing: -.025em; }
    .eyebrow { color: #a78bfa; font-size: .74rem; font-weight: 700; letter-spacing: .14em; }
    .brand { font-size: 1.55rem; font-weight: 750; color: #f8fafc; margin: .2rem 0 0; }
    .brand-copy { color: #94a3b8; font-size: .86rem; line-height: 1.45; margin-bottom: 1.4rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown('<div class="eyebrow">LAMBDA PUBLIC DATA</div>', unsafe_allow_html=True)
    st.markdown('<div class="brand">CloudOps Lens</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="brand-copy">Reliability and GPU portfolio intelligence, '
        "built as an interview prototype.</div>",
        unsafe_allow_html=True,
    )
    selected_view = st.radio(
        "Navigate",
        [
            "Reliability overview",
            "Incident explorer",
            "GPU product explorer",
            "Regional capacity",
            "Open source activity",
        ],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(
        "Public-data prototype · Not an internal Lambda system and not affiliated with or "
        "endorsed by Lambda."
    )
    st.markdown(
        "[Status source](https://status.lambda.ai) · "
        "[Pricing source](https://lambda.ai/service/gpu-cloud) · "
        "[GitHub source](https://github.com/LambdaLabsML)"
    )

if selected_view == "Reliability overview":
    overview()
elif selected_view == "Incident explorer":
    incident_explorer()
elif selected_view == "GPU product explorer":
    gpu_explorer()
elif selected_view == "Regional capacity":
    regional_capacity()
else:
    open_source_activity()
