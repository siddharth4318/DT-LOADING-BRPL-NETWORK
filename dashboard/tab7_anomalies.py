"""
tab7_anomalies.py -- Anomalies tab (for 10,000 DTs with corrected concepts).

Features:
  1. Clean multi-category options for anomaly viewing
  2. Missing values, missing slots, zero values, spikes
  3. Date range and hierarchy filtering
  4. Summary charts with anomaly counts
  5. Detailed tables per category
  6. KPI cards with anomaly statistics
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

import fl_config as cfg
from fl_data_helpers import (
    get_con, get_hierarchy_cached, hard_anomalies_glob, threshold_anomalies_glob,
    missing_slot_days_dt_glob, _globs_literal,
)

CATEGORIES = {
    "Missing Slots / Days": {"source": "missing_slots"},
    "Zero Values (voltage / KWH-with-load)": {"source": "hard", "types": ["ZERO_VOLTAGE", "ZERO_KWH_WITH_LOAD"]},
    "Missing KWH": {"source": "hard", "types": ["MISSING_KWH"]},
    "Extreme Voltage/Current Spikes": {"source": "hard",
                                        "types": ["EXTREME_VOLTAGE_VR", "EXTREME_VOLTAGE_VY", "EXTREME_VOLTAGE_VB",
                                                  "EXTREME_CURRENT_IR", "EXTREME_CURRENT_IY", "EXTREME_CURRENT_IB"]},
    "Negative KVAR": {"source": "hard", "types": ["NEGATIVE_KVAR_KVAR_R", "NEGATIVE_KVAR_KVAR_Y", "NEGATIVE_KVAR_KVAR_B"]},
    "Phase Loss (KW/KVAR)": {"source": "hard",
                              "types": ["PHASE_LOSS_KW_R", "PHASE_LOSS_KW_Y", "PHASE_LOSS_KW_B",
                                        "PHASE_LOSS_KVAR_R", "PHASE_LOSS_KVAR_Y", "PHASE_LOSS_KVAR_B"]},
    "Rolling Threshold Spikes (+-5% band)": {"source": "threshold"},
}


def _kpi_card(label, value, subtitle, color):
    """Styled KPI card for dashboard consistency."""
    st.markdown(
        f"""
        <div style="
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 10px;
            padding: 14px 16px 10px 16px;
            margin-bottom: 4px;
        ">
            <div style="font-size:0.72rem; letter-spacing:0.06em; color:#8a8f98; text-transform:uppercase;">
                {label}
            </div>
            <div style="font-size:1.9rem; font-weight:700; color:{color}; line-height:1.3;">
                {value}
            </div>
            <div style="font-size:0.78rem; color:#8a8f98;">
                {subtitle}
            </div>
            <div style="height:3px; background:{color}; border-radius:2px; margin-top:8px;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _query_hard(con, types, fl, dt, meter, start_date, end_date):
    globs = _globs_literal(hard_anomalies_glob())
    if globs == "[]":
        return pd.DataFrame()
    type_list = ", ".join(f"'{t}'" for t in types)
    where = [f"ANOMALY_TYPE IN ({type_list})", f"DATE >= DATE '{start_date}'", f"DATE <= DATE '{end_date}'"]
    if dt != "All":
        where.append(f"DT_CODE_NEW = '{dt}'")
    if meter != "All":
        where.append(f"MTR_NO = '{meter}'")
    return con.execute(f"SELECT * FROM read_parquet({globs}) WHERE {' AND '.join(where)}").df()


def _query_threshold(con, fl, dt, meter, start_date, end_date):
    globs = _globs_literal(threshold_anomalies_glob())
    if globs == "[]":
        return pd.DataFrame()
    where = [f"DATE >= DATE '{start_date}'", f"DATE <= DATE '{end_date}'"]
    if dt != "All":
        where.append(f"DT_CODE_NEW = '{dt}'")
    if meter != "All":
        where.append(f"MTR_NO = '{meter}'")
    return con.execute(f"SELECT * FROM read_parquet({globs}) WHERE {' AND '.join(where)}").df()


def _query_missing(con, fl, dt, start_date, end_date):
    globs = _globs_literal(missing_slot_days_dt_glob())
    if globs == "[]":
        return pd.DataFrame()
    where = [f"DATE >= DATE '{start_date}'", f"DATE <= DATE '{end_date}'"]
    if dt != "All":
        where.append(f"DT_CODE_NEW = '{dt}'")
    return con.execute(f"SELECT * FROM read_parquet({globs}) WHERE {' AND '.join(where)}").df()


def render():
    st.header("Anomalies")
    st.markdown('<div class="alert at">🔴 Clean multi-category options · Missing values, slots, zero values, spikes · Not cluttered</div>', 
                unsafe_allow_html=True)

    hierarchy = get_hierarchy_cached()
    if hierarchy is None or hierarchy.empty:
        st.warning("Hierarchy cache not available.")
        return

    con = get_con()

    c1, c2, c3 = st.columns(3)
    with c1:
        fl = st.selectbox("FL", options=["All"] + sorted(hierarchy[cfg.COL_FL].dropna().unique()), key="an_fl")
    dt_scope = hierarchy if fl == "All" else hierarchy[hierarchy[cfg.COL_FL] == fl]
    with c2:
        dt = st.selectbox("DT", options=["All"] + sorted(dt_scope[cfg.COL_DT].dropna().unique()), key="an_dt")
    meter_scope = dt_scope if dt == "All" else dt_scope[dt_scope[cfg.COL_DT] == dt]
    with c3:
        meter = st.selectbox("Meter", options=["All"] + sorted(meter_scope[cfg.COL_METER].dropna().unique()), key="an_meter")

    d1, d2 = st.columns(2)
    with d1:
        start_date = st.date_input("Start Date", key="an_start")
    with d2:
        end_date = st.date_input("End Date", key="an_end")

    selected_categories = st.multiselect(
        "Show Anomaly Categories",
        options=list(CATEGORIES.keys()),
        default=["Missing Slots / Days", "Zero Values (voltage / KWH-with-load)"],
        key="an_categories"
    )

    if not selected_categories:
        st.info("Select at least one category above to see results.")
        return

    # Query all selected categories
    all_results = {}
    for cat in selected_categories:
        spec = CATEGORIES[cat]
        if spec["source"] == "hard":
            all_results[cat] = _query_hard(con, spec["types"], fl, dt, meter, start_date, end_date)
        elif spec["source"] == "threshold":
            all_results[cat] = _query_threshold(con, fl, dt, meter, start_date, end_date)
        elif spec["source"] == "missing_slots":
            all_results[cat] = _query_missing(con, fl, dt, start_date, end_date)

    # Calculate total anomalies
    total_anomalies = sum(len(df) for df in all_results.values())
    max_category = max(all_results.keys(), key=lambda k: len(all_results[k])) if all_results else "N/A"
    max_count = len(all_results[max_category]) if all_results else 0

    # KPI cards
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _kpi_card("Total Anomalies", f"{total_anomalies:,}", "across all categories", "#ff6b6b")
    with k2:
        _kpi_card("Categories", f"{len(selected_categories)}", "selected", "#4dd0e1")
    with k3:
        _kpi_card("Top Category", max_category, f"{max_count} anomalies", "#ffd54f")
    with k4:
        _kpi_card("Date Range", f"{(end_date - start_date).days}", "days", "#a78bfa")

    st.divider()

    # Summary chart
    st.subheader("Anomaly Summary by Category")
    summary = pd.DataFrame([{"Category": cat, "Count": len(df)} for cat, df in all_results.items()])
    
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=summary["Category"], 
        y=summary["Count"],
        marker_color="#ff6b6b",
        name="Anomaly Count"
    ))
    fig.update_layout(
        title="Anomaly Counts by Category (Current Filter)",
        xaxis_title="Category", 
        yaxis_title="Count",
        height=350,
        hovermode="x"
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

    st.divider()

    # Detail tables per category
    st.subheader("Detailed Anomaly Records")
    
    for cat, df in all_results.items():
        with st.expander(f"🔴 {cat} ({len(df)} rows)"):
            if df.empty:
                st.caption("No records for this category/filter.")
            else:
                # Add severity color coding if available
                if "SEVERITY" in df.columns:
                    st.caption(f"Severity breakdown: {df['SEVERITY'].value_counts().to_dict()}")
                st.dataframe(df, width='stretch', hide_index=True, height=300)

    # Additional statistics
    st.divider()
    st.subheader("Anomaly Statistics")
    
    stats_data = []
    for cat, df in all_results.items():
        if not df.empty:
            stats_data.append({
                "Category": cat,
                "Count": len(df),
                "Unique DTs": df["DT_CODE_NEW"].nunique() if "DT_CODE_NEW" in df.columns else 0,
                "Unique Meters": df["MTR_NO"].nunique() if "MTR_NO" in df.columns else 0,
            })
    
    if stats_data:
        stats_df = pd.DataFrame(stats_data)
        st.dataframe(stats_df, width='stretch', hide_index=True, height=250)
    else:
        st.info("No anomalies found in the selected date range and filters.")
