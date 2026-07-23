"""
tab1_fl_analytics.py -- FL Analytics tab (for 10,000 DTs).

Shows the FL (SDO_CD) -> DT (DT_CODE_NEW) -> Meter (MTR_NO) hierarchy as a
drill-down: pick an FL, see its DTs and summary stats; pick a DT, see its
meters (including replacement history).

Optimized for large-scale deployments with efficient caching and pagination.
"""

import streamlit as st
import pandas as pd

import fl_config as cfg
from fl_data_helpers import get_hierarchy_cached


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
    st.header("FL Analytics -- Hierarchy")
    st.markdown('<div class="alert at">📊 FL → DT → Meter hierarchy drill-down · Optimized for 10,000+ DTs</div>', 
                unsafe_allow_html=True)

    # Check if cache file exists on disk (instant check, no directory scanning)
    import os
    import fl_config as cfg
    
    cache_exists = os.path.exists(cfg.HIERARCHY_CACHE)
    
    if not cache_exists:
        st.warning("Hierarchy cache not available. Click the button below to build it (takes a few seconds).")
        if st.button("Build Hierarchy Cache"):
            with st.spinner("Building hierarchy cache..."):
                from fl_data_helpers import _build_hierarchy, list_available_months, _months_signature
                months = list_available_months()
                months_sig = _months_signature(months)
                hierarchy = _build_hierarchy(months_sig)
            if hierarchy is not None and not hierarchy.empty:
                st.success("Cache built successfully!")
                st.rerun()
        return
    
    # Load from disk cache (instant read from parquet)
    try:
        hierarchy = pd.read_parquet(cfg.HIERARCHY_CACHE)
    except Exception:
        st.warning("Hierarchy cache file exists but could not be read. Click the button below to rebuild it.")
        if st.button("Rebuild Hierarchy Cache"):
            with st.spinner("Building hierarchy cache..."):
                from fl_data_helpers import _build_hierarchy, list_available_months, _months_signature
                months = list_available_months()
                months_sig = _months_signature(months)
                hierarchy = _build_hierarchy(months_sig)
            if hierarchy is not None and not hierarchy.empty:
                st.success("Cache built successfully!")
                st.rerun()
        return

    has_kva = cfg.COL_KVA_RATING in hierarchy.columns
    has_cat = "DT_CAT" in hierarchy.columns
    has_sts = "DT_STS" in hierarchy.columns

    # Top-level summary
    n_fls = hierarchy[cfg.COL_FL].nunique()
    n_dts = hierarchy[cfg.COL_DT].nunique()
    n_meters = hierarchy[cfg.COL_METER].nunique()
    n_replaced = hierarchy.loc[hierarchy[cfg.COL_IS_METER_REPLACED] == True, cfg.COL_DT].nunique()

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        _kpi_card("Total FLs", f"{n_fls:,}", "feeders", "#4d96ff")
    with k2:
        _kpi_card("Total DTs", f"{n_dts:,}", "distribution transformers", "#2ecc71")
    with k3:
        _kpi_card("Total Meters", f"{n_meters:,}", "smart meters", "#ffd54f")
    with k4:
        _kpi_card("DTs Replaced", f"{n_replaced:,}", "meter replacements", "#ff6b6b")

    st.divider()

    # Drill-down: FL -> DT -> Meter
    fl_list = sorted(hierarchy[cfg.COL_FL].dropna().unique().tolist())
    selected_fl = st.selectbox("Select FL (SDO_CD)", options=["All"] + fl_list, key="fa_fl")

    fl_df = hierarchy if selected_fl == "All" else hierarchy[hierarchy[cfg.COL_FL] == selected_fl]

    agg_dict = {cfg.COL_METER: "nunique"}
    if has_kva:
        agg_dict[cfg.COL_KVA_RATING] = "first"
    if has_cat:
        agg_dict["DT_CAT"] = "first"
    if has_sts:
        agg_dict["DT_STS"] = "first"
    agg_dict[cfg.COL_IS_METER_REPLACED] = "max"

    dt_summary = (
        fl_df.groupby([cfg.COL_FL, cfg.COL_DT], as_index=False)
        .agg(agg_dict)
        .rename(columns={cfg.COL_METER: "N_METERS"})
    )

    st.subheader(f"DTs under {'all FLs' if selected_fl == 'All' else selected_fl} ({len(dt_summary)} DTs)")
    
    # Pagination for large DT lists
    page_size = 50
    total_pages = (len(dt_summary) + page_size - 1) // page_size
    page = st.number_input("Page", min_value=1, max_value=total_pages if total_pages > 0 else 1, value=1, key="fa_page")
    
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    dt_summary_page = dt_summary.iloc[start_idx:end_idx]
    
    st.dataframe(dt_summary_page, width='stretch', hide_index=True, height=400)
    st.caption(f"Showing {start_idx + 1}-{min(end_idx, len(dt_summary))} of {len(dt_summary)} DTs")

    st.divider()

    # Meter drill-down for a chosen DT
    dt_list = sorted(fl_df[cfg.COL_DT].dropna().unique().tolist())
    if not dt_list:
        st.info("No DTs available for this selection.")
        return

    selected_dt = st.selectbox("Select DT to view meters", options=dt_list, key="fa_dt")
    dt_meters = fl_df[fl_df[cfg.COL_DT] == selected_dt].sort_values(cfg.COL_METER_SEQ)

    st.subheader(f"Meters under DT {selected_dt}")
    if dt_meters[cfg.COL_IS_METER_REPLACED].any():
        st.info(f"This DT has had its meter replaced "
                f"({dt_meters[cfg.COL_METER].nunique()} distinct meters across its history).")

    display_cols = [cfg.COL_METER, cfg.COL_METER_SEQ, "dt_meter_rank", cfg.COL_CT_RATIO]
    if has_kva:
        display_cols.append(cfg.COL_KVA_RATING)
    if has_cat:
        display_cols.append("DT_CAT")
    if has_sts:
        display_cols.append("DT_STS")
    display_cols = [c for c in display_cols if c in dt_meters.columns]

    st.dataframe(dt_meters[display_cols], width='stretch', hide_index=True, height=300)
