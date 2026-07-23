"""
tab3_peak_kva.py -- Peak KVA tab.

fl_kva_engine.py exposes two cached functions:
  get_monthly_peak_kva_cached()       -- RAW only, fast (fleet-wide, one
                                          number per meter/month).
  get_monthly_peak_kva_clean_cached() -- RAW + CLEAN, slower the first time
                                          it has to build (row-level
                                          interpolation over anomaly-flagged
                                          + missing slots); instant on
                                          later calls once its own on-disk
                                          parquet cache is fresh.
Note: get_monthly_peak_kva_clean_cached()'s output has NO DT_CODE_NEW
column -- only MTR_NO, YM, PEAK_KVA_RAW, PEAK_KVA_CLEAN.

FIXES in this version:
  1. DUPLICATE WIDGET KEY: same bug as the Load Curve tab -- the year-
     filtered meter dropdown reused the same key as the initial one, so
     Streamlit's error on the second call was silently swallowed and the
     filter never took effect. Fixed by computing the filtered list FIRST,
     then rendering exactly one selectbox.
  2. "Use Clean KVA checkbox shows nothing" / KeyError: 'PEAK_KVA_CLEAN':
     an earlier version called get_monthly_peak_kva_clean_cached()
     unconditionally on every render just to check whether a clean cache
     existed, and separately re-joined clean data through `hierarchy` a
     second, independent time (risking a silent MTR_NO dtype mismatch
     after the parquet round-trip, or a fan-out from duplicate
     (MTR_NO, YM) rows, either of which could leave PEAK_KVA_CLEAN out of
     the resulting frame with no visible error). Fixed by:
       - only calling get_monthly_peak_kva_clean_cached() when the
         checkbox is actually ticked,
       - guaranteeing dt_peak_year always HAS a PEAK_KVA_CLEAN column
         (defaulted to NaN) the moment it's created, before any clean-KVA
         logic runs, so no downstream reference to it can ever KeyError,
       - deduplicating clean_df on (MTR_NO, YM) before merging, to guard
         against a many-to-one fan-out silently corrupting the merge,
       - explicitly checking that the merge produced the column before
         relying on it, and surfacing a clear st.error if it didn't.
"""

import os
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import fl_config as cfg
from fl_data_helpers import get_hierarchy_cached, list_available_months, _read_disk_cache_if_fresh, _months_signature
from fl_kva_engine import get_monthly_peak_kva_cached, get_monthly_peak_kva_clean_cached, MONTHLY_PEAK_CACHE

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


def _kpi_card(label, value, subtitle, color):
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
    st.header("Peak KVA -- Raw vs Clean")
    st.markdown(
        '<div class="alert at">Raw KVA includes every reading as-is. Clean KVA reconstructs missing '
        'and anomaly-flagged slots via gap-length-aware interpolation, then takes the peak of the '
        'rebuilt curve -- computed once and cached fleet-wide, same engine used by CAGR/Forecast.</div>',
        unsafe_allow_html=True,
    )

    hierarchy = get_hierarchy_cached()

    if hierarchy is None or hierarchy.empty:
        st.warning("Hierarchy cache not available.")
        return

    monthly_peak = get_monthly_peak_kva_cached()
    if monthly_peak is None or monthly_peak.empty:
        st.warning("Monthly peak KVA cache not available yet. Click the button below to build it.")
        if st.button("Build Monthly Peak KVA Cache (RAW)"):
            with st.spinner("Building monthly peak KVA cache (RAW only, fast)..."):
                monthly_peak = get_monthly_peak_kva_cached()
            if monthly_peak is not None and not monthly_peak.empty:
                st.success("Cache built successfully!")
                st.rerun()
        return

    c1, c2 = st.columns(2)
    with c1:
        fl = st.selectbox("FL", options=sorted(hierarchy[cfg.COL_FL].dropna().unique()), key="pk_fl")
    dt_options = sorted(hierarchy[hierarchy[cfg.COL_FL] == fl][cfg.COL_DT].dropna().unique())
    with c2:
        selected_dts = st.multiselect("Select DTs (multiple)", options=dt_options, default=dt_options[:1], key="pk_dts")
        if not selected_dts:
            st.warning("Please select at least one DT.")
            return

    # -----------------------------------------------------------------
    # Resolve which column in monthly_peak identifies the DT/meter, and
    # build dt_peak_all for the selected DTs from the RAW cache.
    # -----------------------------------------------------------------
    dt_col = None
    for col in ["DT_CODE_NEW", cfg.COL_DT, "MTR_NO"]:
        if col in monthly_peak.columns:
            dt_col = col
            break

    if dt_col is None:
        st.error(f"Error: No DT/Meter column found in monthly_peak. Available columns: {list(monthly_peak.columns)}")
        return

    if dt_col == "MTR_NO":
        dt_meters = hierarchy[hierarchy[cfg.COL_DT].isin(selected_dts)][cfg.COL_METER].unique()
        dt_peak_all = monthly_peak[monthly_peak[dt_col].isin(dt_meters)].copy()
        dt_peak_all = dt_peak_all.merge(
            hierarchy[[cfg.COL_METER, cfg.COL_DT]].drop_duplicates(),
            left_on="MTR_NO",
            right_on=cfg.COL_METER,
            how="left"
        )
        dt_col = cfg.COL_DT
    else:
        try:
            sample_dt_code = monthly_peak[dt_col].dropna().iloc[0] if not monthly_peak[dt_col].dropna().empty else None
            if sample_dt_code is not None and str(sample_dt_code).isdigit():
                # monthly_peak has numeric codes, selected_dts are alphanumeric -- join through MTR_NO
                dt_meters = hierarchy[hierarchy[cfg.COL_DT].isin(selected_dts)][cfg.COL_METER].unique()
                if "MTR_NO" in monthly_peak.columns:
                    dt_peak_all = monthly_peak[monthly_peak["MTR_NO"].isin(dt_meters)].copy()
                    dt_peak_all = dt_peak_all.merge(
                        hierarchy[[cfg.COL_METER, cfg.COL_DT]].drop_duplicates(),
                        left_on="MTR_NO",
                        right_on=cfg.COL_METER,
                        how="left"
                    )
                    dt_col = cfg.COL_DT
                else:
                    st.error("MTR_NO column not found in monthly_peak. Cannot join with hierarchy.")
                    return
            else:
                dt_peak_all = monthly_peak[monthly_peak[dt_col].isin(selected_dts)].copy()
        except KeyError as e:
            st.error(f"Error filtering by {dt_col}: {e}. Available columns: {list(monthly_peak.columns)}")
            return

    if dt_peak_all.empty:
        st.info(f"No peak KVA data found for selected DTs in FL {fl}.")
        return

    dt_peak_all["YEAR"] = dt_peak_all["YM"] // 100
    years_available = sorted(dt_peak_all["YEAR"].unique())
    year = st.selectbox("Year", options=years_available, index=len(years_available) - 1, key="pk_year")

    dt_peak_year = dt_peak_all[dt_peak_all["YEAR"] == year].copy()
    dt_peak_year["MONTH"] = dt_peak_year["YM"] % 100
    dt_peak_year["MONTH_NAME"] = dt_peak_year["MONTH"].map(MONTH_NAMES)
    dt_peak_year = dt_peak_year.sort_values("MONTH")

    # Guarantee this column always exists, regardless of whether Clean KVA
    # ends up being loaded/merged successfully below -- so every later
    # reference to dt_peak_year["PEAK_KVA_CLEAN"] (KPIs, chart, table) is
    # safe even if the merge is skipped, fails, or is never attempted.
    if "PEAK_KVA_CLEAN" not in dt_peak_year.columns:
        dt_peak_year["PEAK_KVA_CLEAN"] = np.nan

    if dt_peak_year.empty:
        st.info(f"No monthly data for selected DTs in {year}.")
        return

    has_clean = False

    # Track clean cache availability in session state to prevent redundant rebuilds
    if "pk_clean_built" not in st.session_state:
        st.session_state.pk_clean_built = False
    if "pk_clean_data" not in st.session_state:
        st.session_state.pk_clean_data = None

    use_clean = st.checkbox(
        "Use Clean KVA (anomaly-zeroing + interpolation reconstruction; "
        "builds first time then caches instantly)",
        value=st.session_state.pk_clean_built,
        key="pk_use_clean",
    )

    if use_clean:
        # Check if we already have clean data in session state
        if st.session_state.pk_clean_built and st.session_state.pk_clean_data is not None:
            clean_df = st.session_state.pk_clean_data
        else:
            # Use the shared cache-freshness check (respects CACHE_VERSION)
            months = list_available_months()
            months_sig = _months_signature(months)
            cache_fresh = _read_disk_cache_if_fresh(MONTHLY_PEAK_CACHE, months_sig)

            if cache_fresh is not None:
                # Disk cache exists and is fresh (version-compatible) — load it
                clean_df = cache_fresh
                st.session_state.pk_clean_built = True
                st.session_state.pk_clean_data = clean_df
            else:
                # Need to show build button (first time or version mismatch)
                st.markdown(
                    "💡 **Clean KVA** reconstructs curves per (meter, day) by zeroing anomaly-flagged "
                    "slots and applying gap-length-aware interpolation (1-2 slots → linear, "
                    "3-4 → quadratic, 5+ → cubic), **then** taking the peak of the rebuilt curve. "
                    "This is **different** from a simple SQL MAX."
                )
                if st.button("Build Clean KVA Cache (curve reconstruction — one-time)", key="pk_build_clean_btn"):
                    with st.spinner("Building Clean KVA via per-meter curve reconstruction across all months..."):
                        clean_df = get_monthly_peak_kva_clean_cached()
                    if clean_df is not None and not clean_df.empty and "PEAK_KVA_CLEAN" in clean_df.columns:
                        st.session_state.pk_clean_built = True
                        st.session_state.pk_clean_data = clean_df
                        st.success("Clean KVA cache built successfully! Refreshing...")
                        st.rerun()
                    else:
                        st.error("Clean KVA cache build failed. Check terminal for errors. "
                                 "Raw KVA will be shown below.")
                        # Don't return — show raw KVA
                        clean_df = None
                else:
                    return

        if clean_df is None or clean_df.empty or "PEAK_KVA_CLEAN" not in clean_df.columns:
            st.warning("Clean KVA cache returned no data. Showing Raw KVA only.")
        elif "MTR_NO" not in dt_peak_year.columns:
            st.error("MTR_NO column is missing from RAW data, so Clean KVA can't be attached.")
        else:
            # Merge clean KVA into dt_peak_year
            clean_slice = clean_df.loc[:, ["MTR_NO", "YM", "PEAK_KVA_CLEAN"]].copy()
            clean_slice = clean_slice.groupby(["MTR_NO", "YM"], as_index=False)["PEAK_KVA_CLEAN"].max()
            clean_slice["MTR_NO"] = clean_slice["MTR_NO"].astype(str)

            base = dt_peak_year.drop(columns=["PEAK_KVA_CLEAN"], errors="ignore").copy()
            base["MTR_NO"] = base["MTR_NO"].astype(str)

            merged = base.merge(clean_slice, on=["MTR_NO", "YM"], how="left")

            if "PEAK_KVA_CLEAN" not in merged.columns:
                st.error("Clean KVA merge failed. Showing Raw KVA only.")
            else:
                dt_peak_year = merged
                has_clean = dt_peak_year["PEAK_KVA_CLEAN"].notna().any()
                if not has_clean:
                    clean_available = clean_slice['PEAK_KVA_CLEAN'].notna().sum()
                    if clean_available > 0:
                        st.warning(f"Clean KVA has {clean_available:,} values overall, but none match the "
                                  f"selected DT ({selected_dts}) / year ({year}). Try different selection.")
                    else:
                        st.warning("Clean KVA cache returned no non-null values. The curve reconstruction "
                                  "may need tuning. Click 'Build' with `st.write` diagnostics enabled.")

    # -----------------------------------------------------------------
    # KPI cards
    # -----------------------------------------------------------------
    raw_max = dt_peak_year["PEAK_KVA_RAW"].max()
    clean_max = dt_peak_year["PEAK_KVA_CLEAN"].max() if has_clean else np.nan

    if has_clean:
        k1, k2, k3, k4 = st.columns(4)
        valid = dt_peak_year.dropna(subset=["PEAK_KVA_RAW", "PEAK_KVA_CLEAN"])
        valid = valid[valid["PEAK_KVA_RAW"] != 0]
        biggest_delta_row = None
        if not valid.empty:
            valid = valid.copy()
            valid["ABS_DELTA"] = (valid["PEAK_KVA_CLEAN"] - valid["PEAK_KVA_RAW"]).abs()
            biggest_delta_row = valid.loc[valid["ABS_DELTA"].idxmax()]
        avg_pct = ((valid["PEAK_KVA_CLEAN"] - valid["PEAK_KVA_RAW"]) / valid["PEAK_KVA_RAW"] * 100).mean() if not valid.empty else np.nan

        with k1:
            _kpi_card("Raw Max Peak", f"{raw_max:.1f}", "before cleaning", "#ff6b6b")
        with k2:
            _kpi_card("Clean Max Peak", f"{clean_max:.1f}", "after cleaning", "#2ecc71")
        with k3:
            if biggest_delta_row is not None:
                sign = "+" if (biggest_delta_row["PEAK_KVA_CLEAN"] - biggest_delta_row["PEAK_KVA_RAW"]) >= 0 else ""
                delta_val = biggest_delta_row["PEAK_KVA_CLEAN"] - biggest_delta_row["PEAK_KVA_RAW"]
                _kpi_card("Biggest Delta", f"{sign}{delta_val:.1f} KVA",
                           biggest_delta_row["MONTH_NAME"], "#ffd54f")
            else:
                _kpi_card("Biggest Delta", "--", "", "#ffd54f")
        with k4:
            sign = "+" if pd.notna(avg_pct) and avg_pct >= 0 else ""
            _kpi_card("Avg Delta %", f"{sign}{avg_pct:.1f}%" if pd.notna(avg_pct) else "--",
                       "raw -> clean", "#4dd0e1")
    else:
        k1 = st.columns(1)[0]
        with k1:
            _kpi_card("Raw Max Peak", f"{raw_max:.1f}", "before cleaning", "#ff6b6b")
        st.caption("Clean KVA is not yet available for this meter/year -- the clean-peak cache may "
                   "still be building, or this meter has no clean_data for the months shown.")

    st.divider()

    # -----------------------------------------------------------------
    # Month-wise chart - multiple lines for each DT
    # -----------------------------------------------------------------
    st.subheader(f"Month-wise Peak KVA -- {len(selected_dts)} DT(s) in FL {fl}, {year}")
    fig = go.Figure()

    colors = ["#ff6b6b", "#2ecc71", "#3498db", "#9b59b6", "#f39c12", "#e74c3c", "#1abc9c", "#34495e", "#16a085", "#27ae60"]

    for i, dt_code in enumerate(selected_dts):
        dt_data = dt_peak_year[dt_peak_year[dt_col] == dt_code].copy()
        if not dt_data.empty:
            color = colors[i % len(colors)]
            fig.add_trace(go.Scatter(
                x=dt_data["MONTH_NAME"], y=dt_data["PEAK_KVA_RAW"],
                mode="lines+markers", name=f"Raw - {dt_code}",
                line=dict(color=color, width=2, dash="dot"), marker=dict(size=6),
            ))
            if use_clean and has_clean and "PEAK_KVA_CLEAN" in dt_data.columns:
                fig.add_trace(go.Scatter(
                    x=dt_data["MONTH_NAME"], y=dt_data["PEAK_KVA_CLEAN"],
                    mode="lines+markers", name=f"Clean - {dt_code}",
                    line=dict(color=color, width=2.4), marker=dict(size=6),
                ))

    fig.update_layout(
        xaxis=dict(title="Month", categoryorder="array",
                   categoryarray=[MONTH_NAMES[m] for m in range(1, 13)]),
        yaxis_title="Peak KVA", height=500, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

    st.divider()

    # -----------------------------------------------------------------
    # DT-month-wise table with download option
    # -----------------------------------------------------------------
    st.subheader("DT-Month-wise Peak KVA Details")

    pivot_df = dt_peak_year.pivot_table(
        index=dt_col,
        columns="MONTH_NAME",
        values="PEAK_KVA_RAW",
        aggfunc="max"
    )

    month_order = [MONTH_NAMES[m] for m in range(1, 13)]
    pivot_df = pivot_df.reindex(columns=[m for m in month_order if m in pivot_df.columns])

    if use_clean and has_clean and "PEAK_KVA_CLEAN" in dt_peak_year.columns:
        pivot_clean = dt_peak_year.pivot_table(
            index=dt_col,
            columns="MONTH_NAME",
            values="PEAK_KVA_CLEAN",
            aggfunc="max"
        )
        pivot_clean = pivot_clean.reindex(columns=[m for m in month_order if m in pivot_clean.columns])

        pivot_df.columns = [f"Raw_{col}" for col in pivot_df.columns]
        pivot_clean.columns = [f"Clean_{col}" for col in pivot_clean.columns]
        display_df = pd.concat([pivot_df, pivot_clean], axis=1)
    else:
        pivot_df.columns = [f"Raw_{col}" for col in pivot_df.columns]
        display_df = pivot_df

    display_df = display_df.reset_index()
    display_df = display_df.rename(columns={dt_col: "DT"})
    display_df = display_df.round(2)

    st.dataframe(display_df, width='stretch', hide_index=True, height=400)

    csv = display_df.to_csv(index=False)
    st.download_button(
        label="Download DT-Month-wise Peak KVA Data",
        data=csv,
        file_name=f"peak_kva_{fl}_{year}.csv",
        mime="text/csv",
        key="pk_download"
    )

    st.caption("Raw KVA comes from the fast fleet-wide cache (fl_kva_engine.get_monthly_peak_kva_cached). "
               "Clean KVA comes from the separate get_monthly_peak_kva_clean_cached cache, which is slower "
               "to build the first time but instant afterward.")