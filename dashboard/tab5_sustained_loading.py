"""
tab5_sustained_loading.py -- Sustained Loading tab UI.

Imports the engine from fl_sustained_engine.py (single source of truth
for all cache-building logic, concurrency settings, and per-month caching).
"""

import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

import fl_config as cfg
from fl_data_helpers import get_hierarchy_cached
from fl_sustained_engine import (
    get_sustained_cache_raw, get_sustained_cache_clean,
    monthly_band_hours, SUSTAINED_RAW_CACHE, SUSTAINED_CACHE,
)


def _kpi_card(label, value, subtitle, color):
    """Styled KPI card matching reference dashboard."""
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


def render():
    """Render the Sustained Loading tab UI."""
    st.header("Sustained Loading -- Raw vs Clean")
    st.markdown(
        '<div class="alert at">DTs are "sustained loaded" in a band if they accumulate ≥30 cumulative hours '
        'in that band within a calendar month. Raw uses raw_data, Clean uses clean_data with anomaly filtering.</div>',
        unsafe_allow_html=True,
    )

    hierarchy = get_hierarchy_cached()
    if hierarchy is None or hierarchy.empty:
        st.warning("Hierarchy cache not available.")
        return

    # Check if we have the required columns
    if cfg.COL_KVA_RATING not in hierarchy.columns:
        st.error(f"KVA_RATING column not found in hierarchy. Available columns: {list(hierarchy.columns)}")
        return

    # Raw vs Clean toggle
    use_clean = st.checkbox(
        "Use Clean Sustained Loading (requires building CLEAN cache - slower, filters anomalies)",
        value=False,
        key="sl_use_clean",
    )

    # Prevent repeated build triggers
    if "sl_clean_built" not in st.session_state:
        st.session_state.sl_clean_built = False
    
    # Sustained loading is expensive; cache key ensures fast reload
    # We use the disk-cache check to avoid submitting redundant work
    import os as _os
    sl_cache_exists = _os.path.exists(SUSTAINED_RAW_CACHE)
    sl_clean_cache_exists = _os.path.exists(SUSTAINED_CACHE)

    # Load the appropriate cache
    if use_clean:
        if st.session_state.sl_clean_built:
            sustained_df = get_sustained_cache_clean()
        elif sl_clean_cache_exists:
            with st.spinner("Loading Clean Sustained Loading cache..."):
                sustained_df = get_sustained_cache_clean()
            if sustained_df is not None and not sustained_df.empty:
                st.session_state.sl_clean_built = True
        else:
            st.warning("Clean sustained loading data not available. Click below to build it (takes several minutes).")
            if st.button("Build Clean Sustained Loading Cache", key="sl_build_clean_btn"):
                with st.spinner("Building Clean Sustained Loading cache (processes all months)..."):
                    sustained_df = get_sustained_cache_clean()
                if sustained_df is not None and not sustained_df.empty:
                    st.session_state.sl_clean_built = True
                    st.success("Clean sustained loading cache built successfully!")
                    st.rerun()
            if not sl_cache_exists:
                st.info("No RAW cache either — build RAW Sustained Loading first for instant tab loading.")
            return
    else:
        if sl_cache_exists:
            sustained_df = get_sustained_cache_raw()
        else:
            st.warning("Sustained loading cache not available. Click below to build it.")
            if st.button("Build Sustained Loading Cache (RAW only)"):
                with st.spinner("Building RAW Sustained Loading cache (processes all months)..."):
                    sustained_df = get_sustained_cache_raw()
                if sustained_df is not None and not sustained_df.empty:
                    st.success("Cache built successfully!")
                    st.rerun()
            return

    # Roll up to monthly level
    monthly_df = monthly_band_hours(sustained_df)
    if monthly_df.empty:
        st.warning("No sustained loading data available after aggregation.")
        return

    # Merge with hierarchy for FL information
    monthly_df = monthly_df.merge(
        hierarchy[[cfg.COL_DT, cfg.COL_FL]].drop_duplicates(),
        on=cfg.COL_DT,
        how="left"
    )

    # Filter controls
    c1, c2, c3 = st.columns(3)
    with c1:
        fl = st.selectbox(
            "FL",
            options=["All"] + sorted(monthly_df[cfg.COL_FL].dropna().unique()),
            key="sl_fl"
        )
    with c2:
        year = st.selectbox(
            "Year",
            options=["All"] + sorted(monthly_df["YEAR"].dropna().unique().astype(int)),
            key="sl_year"
        )
    with c3:
        band = st.selectbox(
            "Loading Band",
            options=["All"] + [b[0] for b in cfg.LOADING_BANDS],
            key="sl_band"
        )

    # Apply filters
    filtered_df = monthly_df.copy()
    if fl != "All":
        filtered_df = filtered_df[filtered_df[cfg.COL_FL] == fl]
    if year != "All":
        filtered_df = filtered_df[filtered_df["YEAR"] == int(year)]
    if band != "All":
        filtered_df = filtered_df[filtered_df["BAND"] == band]

    if filtered_df.empty:
        st.info("No data matches the selected filters.")
        return
    
    # DT multi-select
    dt_options = sorted(filtered_df[cfg.COL_DT].dropna().unique())
    selected_dts = st.multiselect("Select DTs (multiple)", options=dt_options, default=dt_options[:5], key="sl_dts")
    if not selected_dts:
        st.warning("Please select at least one DT.")
        return
    
    # Filter by selected DTs
    filtered_df = filtered_df[filtered_df[cfg.COL_DT].isin(selected_dts)]

    # KPI cards
    total_dts = filtered_df[cfg.COL_DT].nunique()
    total_hours = filtered_df["hours_in_band"].sum()
    avg_hours_per_dt = filtered_df.groupby(cfg.COL_DT)["hours_in_band"].sum().mean()
    sustained_dts = (filtered_df.groupby(cfg.COL_DT)["hours_in_band"].sum() >= cfg.SUSTAINED_MIN_HOURS_PER_MONTH).sum()

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _kpi_card("Total DTs", f"{total_dts}", "in filtered view", "#4d96ff")
    with k2:
        _kpi_card("Total Hours", f"{total_hours:.1f}", "across all DTs", "#ff6b6b")
    with k3:
        _kpi_card("Avg Hours/DT", f"{avg_hours_per_dt:.1f}", "per DT in band", "#ffd54f")
    with k4:
        _kpi_card("Sustained DTs", f"{sustained_dts}", f"≥{cfg.SUSTAINED_MIN_HOURS_PER_MONTH}h/month", "#2ecc71")

    st.divider()

    # Chart: Month-wise hours by DT (multiple lines)
    st.subheader(f"Month-wise Sustained Loading Hours -- {len(selected_dts)} DT(s) ({'Clean' if use_clean else 'Raw'})")
    
    # Add month names
    MONTH_NAMES = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
    }
    filtered_df["MONTH"] = filtered_df["YM"] % 100
    filtered_df["MONTH_NAME"] = filtered_df["MONTH"].map(MONTH_NAMES)
    
    fig = go.Figure()
    
    # Add a line for each DT
    colors = ["#ff6b6b", "#2ecc71", "#3498db", "#9b59b6", "#f39c12", "#e74c3c", "#1abc9c", "#34495e", "#16a085", "#27ae60"]
    
    for i, dt_code in enumerate(selected_dts):
        dt_data = filtered_df[filtered_df[cfg.COL_DT] == dt_code].sort_values("MONTH")
        if not dt_data.empty:
            color = colors[i % len(colors)]
            fig.add_trace(go.Scatter(
                x=dt_data["MONTH_NAME"], y=dt_data["hours_in_band"],
                mode="lines+markers", name=f"DT {dt_code}",
                line=dict(color=color, width=2), marker=dict(size=6)
            ))
    
    fig.add_hline(
        y=cfg.SUSTAINED_MIN_HOURS_PER_MONTH,
        line_dash="dash",
        line_color="#ff6b6b",
        annotation_text=f"Sustained Threshold ({cfg.SUSTAINED_MIN_HOURS_PER_MONTH}h)"
    )
    fig.update_layout(
        xaxis=dict(title="Month", categoryorder="array",
                   categoryarray=[MONTH_NAMES[m] for m in range(1, 13)]),
        yaxis_title="Hours in Band",
        hovermode="x unified",
        height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

    st.divider()

    # DT-month-wise table with download option
    st.subheader("DT-Month-wise Sustained Loading Details")
    
    # Pivot table: DT as rows, Months as columns
    pivot_df = filtered_df.pivot_table(
        index=cfg.COL_DT,
        columns="MONTH_NAME",
        values="hours_in_band",
        aggfunc="sum"
    )
    
    # Reorder columns by month
    month_order = [MONTH_NAMES[m] for m in range(1, 13)]
    pivot_df = pivot_df.reindex(columns=[m for m in month_order if m in pivot_df.columns])
    
    display_df = pivot_df.reset_index()
    display_df = display_df.rename(columns={cfg.COL_DT: "DT"})
    display_df = display_df.round(2)
    
    st.dataframe(display_df, width='stretch', hide_index=True, height=400)
    
    # Download button
    csv = display_df.to_csv(index=False)
    st.download_button(
        label="Download DT-Month-wise Sustained Loading Data",
        data=csv,
        file_name=f"sustained_loading_{fl}_{year}_{band}.csv",
        mime="text/csv",
        key="sl_download"
    )

    st.divider()

    # Data table
    st.subheader("Detailed Sustained Loading Data")
    display_df = filtered_df.sort_values([cfg.COL_DT, "YEAR", "YM"]).copy()
    display_df["SUSTAINED"] = display_df["hours_in_band"] >= cfg.SUSTAINED_MIN_HOURS_PER_MONTH
    st.dataframe(display_df.round(2), width='stretch', hide_index=True, height=400)

    st.caption("Sustained Loading = DT accumulates ≥30 cumulative hours in a loading band within a calendar month.")