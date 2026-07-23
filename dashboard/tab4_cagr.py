"""
tab4_cagr.py -- CAGR & Growth tab (for 10,000 DTs with corrected concepts).

Features:
  1. SDO code wise and FL wise analysis
  2. Year-over-Year growth calculation
  3. Approach: Coincidental Peak Method
     - Daily Sum: Sum KVA of all slots in entire day
     - Monthly Peak: Day with highest daily sum = coincidental peak for that month
     - Yearly Peak: Month with highest monthly peak = coincidental peak for that year
  4. Calculate CAGR from yearly coincidental peaks
  5. FL-wise, DT-wise, and Subcluster (DT_CAT) wise analysis
  6. KPI cards with growth metrics
  7. Bar charts for growth visualization
"""

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import fl_config as cfg
from fl_data_helpers import get_hierarchy_cached
from fl_kva_engine import get_monthly_peak_kva_cached, get_coincidental_peak_kva_cached

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
}

YEAR_COLORS = ["#4d96ff", "#2ecc71", "#ffd54f", "#ff9f43", "#a78bfa", "#4dd0e1", "#ff6b6b"]


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


def _yearly_peak_from_monthly(monthly_peak, group_col, value_col):
    """
    Calculate yearly peak KVA from monthly peaks.
    Yearly peak = MAX of that year's monthly peak KVA values.
    """
    # Check if the requested group_col exists, otherwise try alternatives
    actual_group_col = group_col
    if group_col not in monthly_peak.columns:
        for col in ["DT_CODE_NEW", "MTR_NO"]:
            if col in monthly_peak.columns:
                actual_group_col = col
                break
    
    monthly_peak["YEAR"] = monthly_peak["YM"] // 100
    yearly = monthly_peak.groupby([actual_group_col, "YEAR"], as_index=False)[value_col].max()
    yearly = yearly.rename(columns={value_col: f"YEARLY_{value_col}"})
    # Rename the group column back to the expected name if different
    if actual_group_col != group_col:
        yearly = yearly.rename(columns={actual_group_col: group_col})
    return yearly


def _add_yoy(df, group_col, value_col):
    """Add year-over-year growth percentage."""
    df = df.sort_values([group_col, "YEAR"]).copy()
    df["PREV_YEAR_PEAK"] = df.groupby(group_col)[value_col].shift(1)
    df["YOY_GROWTH_PCT"] = (df[value_col] / df["PREV_YEAR_PEAK"] - 1) * 100
    return df


def render():
    st.header("CAGR & Growth")
    st.markdown('<div class="alert at">📊 Coincidental Peak Method CAGR · FL/DT/Subcluster-wise · Daily Sum → Monthly Peak → Yearly Peak</div>', 
                unsafe_allow_html=True)

    hierarchy = get_hierarchy_cached()
    coincidental_peak = get_coincidental_peak_kva_cached()
    
    if hierarchy is None or hierarchy.empty:
        st.warning("Hierarchy cache not available.")
        return
    
    if coincidental_peak is None or coincidental_peak.empty:
        st.warning("Coincidental peak KVA cache not available yet. Click the button below to build it.")
        if st.button("Build Coincidental Peak KVA Cache"):
            with st.spinner("Building coincidental peak KVA cache (this may take several minutes)..."):
                coincidental_peak = get_coincidental_peak_kva_cached()
            if coincidental_peak is not None and not coincidental_peak.empty:
                st.success("Cache built successfully!")
                st.rerun()
        return

    # Extract yearly coincidental peaks from cache
    dt_yearly = coincidental_peak[coincidental_peak["LEVEL"] == "DT_YEARLY"].copy()
    fl_yearly = coincidental_peak[coincidental_peak["LEVEL"] == "FL_YEARLY"].copy()
    cat_yearly = coincidental_peak[coincidental_peak["LEVEL"] == "CAT_YEARLY"].copy()
    
    # Add YoY growth
    if not dt_yearly.empty:
        dt_yearly = _add_yoy(dt_yearly, "DT_CODE_NEW", "YEARLY_COINCIDENTAL_PEAK_DT")
        dt_yearly = dt_yearly.rename(columns={"YEARLY_COINCIDENTAL_PEAK_DT": "YEARLY_PEAK"})
    
    if not fl_yearly.empty:
        fl_yearly = _add_yoy(fl_yearly, "SDO_CD", "YEARLY_COINCIDENTAL_PEAK_FL")
        fl_yearly = fl_yearly.rename(columns={"YEARLY_COINCIDENTAL_PEAK_FL": "YEARLY_PEAK"})
    
    if not cat_yearly.empty:
        cat_yearly = _add_yoy(cat_yearly, "DT_CAT", "YEARLY_COINCIDENTAL_PEAK_CAT")
        cat_yearly = cat_yearly.rename(columns={"YEARLY_COINCIDENTAL_PEAK_CAT": "YEARLY_PEAK"})

    # Check if we have FL data before showing the view selector
    fl_options = sorted(fl_yearly[cfg.COL_FL].dropna().unique()) if not fl_yearly.empty else []
    if not fl_options:
        st.error("No FL (SDO_CD) data available in coincidental peak cache.")
        return

    view = st.radio("View", options=["FL (SDO_CD) level", "DT level", "Subcluster (DT_CAT) level"], horizontal=True, key="cagr_view")

    if view == "FL (SDO_CD) level":
        c1, c2 = st.columns(2)
        with c1:
            fl = st.selectbox("Select FL", options=fl_options, key="cagr_fl")
        
        fl_data = fl_yearly[fl_yearly[cfg.COL_FL] == fl].sort_values("YEAR")
        
        if not fl_data.empty:
            # KPI cards
            avg_growth = fl_data["YOY_GROWTH_PCT"].mean()
            max_growth = fl_data["YOY_GROWTH_PCT"].max()
            min_growth = fl_data["YOY_GROWTH_PCT"].min()
            
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                _kpi_card("Avg YoY Growth", f"{avg_growth:.1f}%", "across all years", "#ff6b6b")
            with k2:
                _kpi_card("Max YoY Growth", f"{max_growth:.1f}%", "best year", "#ffd54f")
            with k3:
                _kpi_card("Min YoY Growth", f"{min_growth:.1f}%", "worst year", "#4dd0e1")
            with k4:
                _kpi_card("Years of Data", f"{len(fl_data)}", "years tracked", "#a78bfa")

        st.divider()

        # Yearly growth chart
        st.subheader(f"FL {fl} -- Coincidental Peak KVA and YoY Growth")
        
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=fl_data["YEAR"], y=fl_data["YEARLY_PEAK"], 
            name="Coincidental Peak KVA",
            marker_color="#ff6b6b"
        ))
        fig.add_trace(go.Scatter(
            x=fl_data["YEAR"], y=fl_data["YOY_GROWTH_PCT"], 
            name="YoY Growth %",
            yaxis="y2", mode="lines+markers", 
            line=dict(color="#ffd54f", width=2.5), marker=dict(size=8)
        ))
        fig.update_layout(
            yaxis=dict(title="Yearly Coincidental Peak KVA", gridcolor="rgba(255,255,255,0.035)"),
            yaxis2=dict(title="YoY Growth %", overlaying="y", side="right", gridcolor="rgba(255,255,255,0.035)"),
            xaxis=dict(title="Year", gridcolor="rgba(255,255,255,0.035)"),
            hovermode="x unified", height=450,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

        with st.expander("📋 Full Yearly Growth Table"):
            display_df = fl_data[["YEAR", "YEARLY_PEAK", "YOY_GROWTH_PCT"]].copy()
            display_df = display_df.round(2)
            st.dataframe(display_df, width='stretch', hide_index=True, height=300)

    elif view == "DT level":
        c1, c2 = st.columns(2)
        with c1:
            fl = st.selectbox("Filter by FL", options=["All"] + fl_options, key="cagr_dt_fl")
        
        # Get unique DT to SDO_CD mapping from hierarchy
        dt_to_fl = hierarchy[[cfg.COL_DT, cfg.COL_FL]].drop_duplicates()
        
        if fl == "All":
            scoped_dt = dt_yearly
        else:
            # Join dt_yearly with hierarchy to get SDO_CD for filtering
            scoped_dt = dt_yearly.merge(dt_to_fl, on=cfg.COL_DT, how="left")
            scoped_dt = scoped_dt[scoped_dt[cfg.COL_FL] == fl].copy()
            scoped_dt = scoped_dt.drop(columns=[cfg.COL_FL]) if cfg.COL_FL in scoped_dt.columns else scoped_dt
        
        with c2:
            dt_options = sorted(scoped_dt["DT_CODE_NEW"].dropna().unique())
            selected_dts = st.multiselect("Select DTs (multiple)", options=dt_options, default=dt_options[:1], key="cagr_dts")
            if not selected_dts:
                st.warning("Please select at least one DT.")
                return
        
        dt_data = scoped_dt[scoped_dt["DT_CODE_NEW"].isin(selected_dts)].sort_values(["DT_CODE_NEW", "YEAR"])
        
        if not dt_data.empty:
            # KPI cards
            avg_growth = dt_data["YOY_GROWTH_PCT"].mean()
            
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                _kpi_card("Avg YoY Growth", f"{avg_growth:.1f}%", "across all years", "#ff6b6b")
            with k2:
                _kpi_card("Years of Data", f"{len(dt_data['YEAR'].unique())}", "years tracked", "#a78bfa")
            with k3:
                _kpi_card("DTs Selected", f"{len(selected_dts)}", "DTs in view", "#ffd54f")
            with k4:
                _kpi_card("Total Records", f"{len(dt_data)}", "DT-year pairs", "#4dd0e1")

        st.divider()

        # Yearly growth chart - multiple lines for each DT
        st.subheader(f"{len(selected_dts)} DT(s) -- Coincidental Peak KVA and YoY Growth")
        
        fig = go.Figure()
        
        # Add a line for each DT
        colors = ["#ff6b6b", "#2ecc71", "#3498db", "#9b59b6", "#f39c12", "#e74c3c", "#1abc9c", "#34495e", "#16a085", "#27ae60"]
        
        for i, dt_code in enumerate(selected_dts):
            dt_single = dt_data[dt_data["DT_CODE_NEW"] == dt_code].sort_values("YEAR")
            if not dt_single.empty:
                color = colors[i % len(colors)]
                fig.add_trace(go.Scatter(
                    x=dt_single["YEAR"], y=dt_single["YEARLY_PEAK"], 
                    name=f"Peak - {dt_code}",
                    mode="lines+markers",
                    line=dict(color=color, width=2, dash="dot"), marker=dict(size=6)
                ))
                fig.add_trace(go.Scatter(
                    x=dt_single["YEAR"], y=dt_single["YOY_GROWTH_PCT"], 
                    name=f"YoY % - {dt_code}",
                    yaxis="y2", mode="lines+markers", 
                    line=dict(color=color, width=2.5), marker=dict(size=6)
                ))
        
        fig.update_layout(
            yaxis=dict(title="Yearly Coincidental Peak KVA", gridcolor="rgba(255,255,255,0.035)"),
            yaxis2=dict(title="YoY Growth %", overlaying="y", side="right", gridcolor="rgba(255,255,255,0.035)"),
            xaxis=dict(title="Year", gridcolor="rgba(255,255,255,0.035)"),
            hovermode="x unified", height=500,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
        
        st.divider()
        
        # DT-year-wise table with download option
        st.subheader("DT-Year-wise Coincidental Peak KVA and Growth Details")
        
        # Pivot table: DT as rows, Years as columns
        pivot_df = dt_data.pivot_table(
            index="DT_CODE_NEW",
            columns="YEAR",
            values="YEARLY_PEAK",
            aggfunc="max"
        )
        
        # Add YoY growth columns
        pivot_yoy = dt_data.pivot_table(
            index="DT_CODE_NEW",
            columns="YEAR",
            values="YOY_GROWTH_PCT",
            aggfunc="max"
        )
        
        # Merge peak and YoY
        pivot_df.columns = [f"Peak_{col}" for col in pivot_df.columns]
        pivot_yoy.columns = [f"YoY_{col}" for col in pivot_yoy.columns]
        display_df = pd.concat([pivot_df, pivot_yoy], axis=1)
        
        display_df = display_df.reset_index()
        display_df = display_df.rename(columns={"DT_CODE_NEW": "DT"})
        display_df = display_df.round(2)
        
        st.dataframe(display_df, width='stretch', hide_index=True, height=400)
        
        # Download button
        csv = display_df.to_csv(index=False)
        st.download_button(
            label="Download DT-Year-wise CAGR Data",
            data=csv,
            file_name=f"cagr_coincidental_{len(selected_dts)}_dts.csv",
            mime="text/csv",
            key="cagr_download"
        )

    elif view == "Subcluster (DT_CAT) level":
        cat_options = sorted(cat_yearly["DT_CAT"].dropna().unique()) if not cat_yearly.empty else []
        if not cat_options:
            st.warning("No DT_CAT data available in coincidental peak cache.")
            return
        
        selected_cats = st.multiselect("Select Subclusters (DT_CAT)", options=cat_options, default=cat_options[:3], key="cagr_cats")
        if not selected_cats:
            st.warning("Please select at least one subcluster.")
            return
        
        cat_data = cat_yearly[cat_yearly["DT_CAT"].isin(selected_cats)].sort_values(["DT_CAT", "YEAR"])
        
        if not cat_data.empty:
            # KPI cards
            avg_growth = cat_data["YOY_GROWTH_PCT"].mean()
            
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                _kpi_card("Avg YoY Growth", f"{avg_growth:.1f}%", "across all years", "#ff6b6b")
            with k2:
                _kpi_card("Years of Data", f"{len(cat_data['YEAR'].unique())}", "years tracked", "#a78bfa")
            with k3:
                _kpi_card("Subclusters Selected", f"{len(selected_cats)}", "categories in view", "#ffd54f")
            with k4:
                _kpi_card("Total Records", f"{len(cat_data)}", "cat-year pairs", "#4dd0e1")

        st.divider()

        # Yearly growth chart - multiple lines for each subcluster
        st.subheader(f"{len(selected_cats)} Subcluster(s) -- Coincidental Peak KVA and YoY Growth")
        
        fig = go.Figure()
        
        # Add a line for each subcluster
        colors = ["#ff6b6b", "#2ecc71", "#3498db", "#9b59b6", "#f39c12", "#e74c3c", "#1abc9c", "#34495e", "#16a085", "#27ae60"]
        
        for i, cat_code in enumerate(selected_cats):
            cat_single = cat_data[cat_data["DT_CAT"] == cat_code].sort_values("YEAR")
            if not cat_single.empty:
                color = colors[i % len(colors)]
                fig.add_trace(go.Scatter(
                    x=cat_single["YEAR"], y=cat_single["YEARLY_PEAK"], 
                    name=f"Peak - {cat_code}",
                    mode="lines+markers",
                    line=dict(color=color, width=2, dash="dot"), marker=dict(size=6)
                ))
                fig.add_trace(go.Scatter(
                    x=cat_single["YEAR"], y=cat_single["YOY_GROWTH_PCT"], 
                    name=f"YoY % - {cat_code}",
                    yaxis="y2", mode="lines+markers", 
                    line=dict(color=color, width=2.5), marker=dict(size=6)
                ))
        
        fig.update_layout(
            yaxis=dict(title="Yearly Coincidental Peak KVA", gridcolor="rgba(255,255,255,0.035)"),
            yaxis2=dict(title="YoY Growth %", overlaying="y", side="right", gridcolor="rgba(255,255,255,0.035)"),
            xaxis=dict(title="Year", gridcolor="rgba(255,255,255,0.035)"),
            hovermode="x unified", height=500,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
        
        st.divider()
        
        # Subcluster-year-wise table with download option
        st.subheader("Subcluster-Year-wise Coincidental Peak KVA and Growth Details")
        
        # Pivot table: DT_CAT as rows, Years as columns
        pivot_df = cat_data.pivot_table(
            index="DT_CAT",
            columns="YEAR",
            values="YEARLY_PEAK",
            aggfunc="max"
        )
        
        # Add YoY growth columns
        pivot_yoy = cat_data.pivot_table(
            index="DT_CAT",
            columns="YEAR",
            values="YOY_GROWTH_PCT",
            aggfunc="max"
        )
        
        # Merge peak and YoY
        pivot_df.columns = [f"Peak_{col}" for col in pivot_df.columns]
        pivot_yoy.columns = [f"YoY_{col}" for col in pivot_yoy.columns]
        display_df = pd.concat([pivot_df, pivot_yoy], axis=1)
        
        display_df = display_df.reset_index()
        display_df = display_df.rename(columns={"DT_CAT": "Subcluster"})
        display_df = display_df.round(2)
        
        st.dataframe(display_df, width='stretch', hide_index=True, height=400)
        
        # Download button
        csv = display_df.to_csv(index=False)
        st.download_button(
            label="Download Subcluster-Year-wise CAGR Data",
            data=csv,
            file_name=f"cagr_coincidental_{len(selected_cats)}_subclusters.csv",
            mime="text/csv",
            key="cagr_cat_download"
        )
    
    st.caption("Coincidental Peak Method: Daily Sum → Monthly Peak (max daily sum) → Yearly Peak (max monthly peak). CAGR calculated from yearly coincidental peaks.")
