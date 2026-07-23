"""
tab6_forecast.py -- Forecast tab (for 10,000 DTs with corrected concepts).

Features:
  1. 2026 Forecast with best regression method
  2. Auto-selection of best model by leave-one-out error
  3. DT-level and FL-level forecasts
  4. Historical data visualization with forecast projection
  5. KPI cards with forecast metrics
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

import fl_config as cfg
from fl_data_helpers import get_hierarchy_cached
from fl_kva_engine import get_monthly_peak_kva_cached
from fl_forecast_engine import fit_best_model
from tab4_cagr import _yearly_peak_from_monthly


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


def render():
    st.header("2027 Forecast")
    st.markdown('<div class="alert at">🔮 Best regression method · Auto-selected by leave-one-out error · DT & FL rollup forecasts</div>', 
                unsafe_allow_html=True)

    hierarchy = get_hierarchy_cached()
    
    if hierarchy is None or hierarchy.empty:
        st.warning("Hierarchy cache not available.")
        return
    
    monthly_peak = get_monthly_peak_kva_cached()
    if monthly_peak is None or monthly_peak.empty:
        st.warning("Monthly peak KVA cache not available yet. Please visit the Peak KVA tab first to build the cache.")
        return
    
    # Forecast uses clean KVA, so we need to load the clean cache
    from fl_kva_engine import get_monthly_peak_kva_clean_cached
    monthly_peak_clean = get_monthly_peak_kva_clean_cached()
    if monthly_peak_clean is None or monthly_peak_clean.empty or "PEAK_KVA_CLEAN" not in monthly_peak_clean.columns:
        st.warning("Clean KVA cache not available. Please enable 'Use Clean KVA' in the Peak KVA tab first to build the clean cache.")
        return

    # Calculate yearly peaks from monthly peaks for clean KVA
    yearly_dt_clean = _yearly_peak_from_monthly(monthly_peak_clean, cfg.COL_DT, "PEAK_KVA_CLEAN")
    yearly_dt_clean = yearly_dt_clean.merge(hierarchy[[cfg.COL_DT, cfg.COL_FL]].drop_duplicates(),
                                 on=cfg.COL_DT, how="left")

    max_year = int(yearly_dt_clean["YEAR"].max())
    target_year = 2027  # Updated to 2027 as we have data through April-June 2026

    c1, c2, c3 = st.columns(3)
    with c1:
        fl = st.selectbox("FL", options=sorted(yearly_dt_clean[cfg.COL_FL].dropna().unique()), key="fc_fl")
    dt_options = sorted(yearly_dt_clean[yearly_dt_clean[cfg.COL_FL] == fl][cfg.COL_DT].dropna().unique())
    with c2:
        dt = st.selectbox("DT", options=dt_options, key="fc_dt")
    with c3:
        st.info(f"Forecast Year: {target_year}")

    sub = yearly_dt_clean[yearly_dt_clean[cfg.COL_DT] == dt].sort_values("YEAR")
    if len(sub) < 3:
        st.info(f"DT {dt} only has {len(sub)} year(s) of data -- need at least 3 yearly points to forecast.")
        return

    result = fit_best_model(sub["YEAR"].tolist(), sub["YEARLY_PEAK_KVA_CLEAN"].tolist(), target_year)
    if result is None:
        st.warning("Could not fit a forecast model for this DT (insufficient/degenerate data).")
        return

    # KPI cards for DT forecast
    current_peak = sub["YEARLY_PEAK_KVA_CLEAN"].iloc[-1]
    forecast_peak = result["predicted_value"]
    growth_pct = ((forecast_peak / current_peak) - 1) * 100 if current_peak > 0 else 0
    
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _kpi_card("Current Peak", f"{current_peak:.1f}", f"{max_year} KVA", "#4d96ff")
    with k2:
        _kpi_card(f"{target_year} Forecast", f"{forecast_peak:.1f}", "predicted KVA", "#2ecc71")
    with k3:
        sign = "+" if growth_pct >= 0 else ""
        _kpi_card("Growth", f"{sign}{growth_pct:.1f}%", "forecast vs current", "#ffd54f")
    with k4:
        _kpi_card("Best Model", result["method"], f"LOO error: {result['loo_error']:.2f}", "#a78bfa")

    st.divider()

    # DT forecast chart
    st.subheader(f"DT {dt} -- Peak KVA Forecast")
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=result["history_years"], 
        y=result["history_values"],
        mode="markers+lines", 
        name="Actual Yearly Peak KVA", 
        marker=dict(size=10, color="#4d96ff"),
        line=dict(width=2, color="#4d96ff")
    ))
    fig.add_trace(go.Scatter(
        x=result["plot_years"], 
        y=result["plot_values"],
        mode="lines+markers", 
        name=f"Best Fit ({result['method']})",
        line=dict(dash="dash", width=2, color="#ffd54f"),
        marker=dict(size=6, color="#ffd54f")
    ))
    fig.add_trace(go.Scatter(
        x=[target_year], 
        y=[result["predicted_value"]],
        mode="markers", 
        name=f"{target_year} Forecast",
        marker=dict(size=16, color="#ff6b6b", symbol="star")
    ))
    fig.update_layout(
        title=f"DT {dt} -- Peak KVA Forecast ({result['method']} model)",
        xaxis_title="Year", 
        yaxis_title="Peak KVA",
        height=450,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

    st.caption(f"**Model Details:** {result['method']} selected by lowest leave-one-out squared error ({result['loo_error']:.2f}) "
               f"among linear / quadratic / exponential candidates.")

    with st.expander("📋 Forecast Details"):
        details_df = pd.DataFrame({
            "Year": result["history_years"] + [target_year],
            "Type": ["Actual"] * len(result["history_years"]) + ["Forecast"],
            "Peak KVA": result["history_values"] + [result["predicted_value"]]
        })
        st.dataframe(details_df, width='stretch', hide_index=True, height=250)

    st.divider()

    # FL-level rollup forecast
    st.subheader(f"FL {fl} Rollup Forecast")
    st.markdown('<div class="alert at">📊 FL forecast = sum of individual DT forecasts (non-coincident sum for capacity planning)</div>', 
                unsafe_allow_html=True)
    
    fl_dts = yearly_dt_clean[yearly_dt_clean[cfg.COL_FL] == fl][cfg.COL_DT].unique()
    fl_forecast_total = 0.0
    rows = []
    forecastable_dts = 0
    
    for d in fl_dts:
        d_sub = yearly_dt_clean[yearly_dt_clean[cfg.COL_DT] == d].sort_values("YEAR")
        if len(d_sub) < 3:
            continue
        r = fit_best_model(d_sub["YEAR"].tolist(), d_sub["YEARLY_PEAK_KVA_CLEAN"].tolist(), target_year)
        if r is None:
            continue
        fl_forecast_total += r["predicted_value"]
        current_dt_peak = d_sub["YEARLY_PEAK_KVA_CLEAN"].iloc[-1]
        dt_growth = ((r["predicted_value"] / current_dt_peak) - 1) * 100 if current_dt_peak > 0 else 0
        rows.append({
            "DT_CODE_NEW": d, 
            "METHOD": r["method"], 
            "CURRENT_PEAK": current_dt_peak,
            "FORECAST_KVA": r["predicted_value"],
            "GROWTH_PCT": dt_growth
        })
        forecastable_dts += 1

    if rows:
        fl_df = pd.DataFrame(rows).sort_values("FORECAST_KVA", ascending=False)
        
        # FL KPI cards
        fl_current = fl_df["CURRENT_PEAK"].sum()
        fl_growth = ((fl_forecast_total / fl_current) - 1) * 100 if fl_current > 0 else 0
        
        fk1, fk2, fk3, fk4 = st.columns(4)
        with fk1:
            _kpi_card("FL Current Peak", f"{fl_current:.1f}", f"{max_year} KVA", "#4d96ff")
        with fk2:
            _kpi_card(f"FL {target_year} Forecast", f"{fl_forecast_total:.1f}", "predicted KVA", "#2ecc71")
        with fk3:
            sign = "+" if fl_growth >= 0 else ""
            _kpi_card("FL Growth", f"{sign}{fl_growth:.1f}%", "forecast vs current", "#ffd54f")
        with fk4:
            _kpi_card("Forecastable DTs", f"{forecastable_dts}", f"of {len(fl_dts)} total", "#a78bfa")

        # FL forecast chart
        fig_fl = go.Figure()
        fig_fl.add_trace(go.Bar(
            x=fl_df["DT_CODE_NEW"], 
            y=fl_df["CURRENT_PEAK"],
            name=f"{max_year} Current Peak",
            marker_color="#4d96ff"
        ))
        fig_fl.add_trace(go.Bar(
            x=fl_df["DT_CODE_NEW"], 
            y=fl_df["FORECAST_KVA"],
            name=f"{target_year} Forecast",
            marker_color="#2ecc71"
        ))
        fig_fl.update_layout(
            title=f"FL {fl} -- Current vs Forecast Peak KVA by DT",
            xaxis_title="DT Code", 
            yaxis_title="Peak KVA",
            barmode="group",
            height=450,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig_fl, width='stretch', config={"displayModeBar": False})

        with st.expander("📋 FL Forecast Details Table"):
            display_df = fl_df.copy()
            display_df["GROWTH_PCT"] = display_df["GROWTH_PCT"].round(1)
            display_df.columns = ["DT", "Model", "Current Peak", "Forecast", "Growth %"]
            st.dataframe(display_df, width='stretch', hide_index=True, height=300)
    else:
        st.info("Not enough DTs with 3+ years of history under this FL for a rollup forecast.")
