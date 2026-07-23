"""
tab2_load_curve.py -- Load Curve tab.

FIXES in this version (see inline comments at each spot):
  1. DUPLICATE WIDGET KEY: the meter dropdown was created TWICE with the same
     key ("lc_meter") -- once unfiltered, once filtered by date. Streamlit's
     DuplicateWidgetID error on the second call was being silently swallowed
     by a bare `except Exception: pass`, so the date-filtered dropdown never
     actually took effect -- you were always stuck picking from every meter
     across the DT's whole history. Fixed by computing the filtered options
     FIRST, then creating exactly ONE selectbox.
  2. ANOMALY MARKERS not showing: flag_anomalies_threshold() does
     .reset_index(), which turns TIME_SLOT back into a normal column -- but
     the marker code was reading `anom_rows.index` (just row numbers 0,1,2..)
     instead of `anom_rows["TIME_SLOT"]`. Markers were being plotted at
     x-positions that don't exist on the categorical time-slot axis.
  3. CLEAN/INTERPOLATED CURVE not shown properly: the chart's x-axis was
     built from `actual.index` (only slots with real data), while the
     filled/interpolated curve always covers the FULL slot grid. Reindexing
     the filled curve back down to `actual`'s sparse index discarded the
     very points that were filled in. Now the x-axis is the full slot grid
     for both traces, with the raw trace using connectgaps=False (so real
     gaps visibly break the line) and the filled trace using
     connectgaps=True (so it reads as the "repaired" curve).
  4. Buggy hand-rolled cubic interpolation (mismatched boolean-mask
     indexing, silently falling back to linear every time) replaced with
     the already-correct, already-tested fl_interp_engine.interpolate_series
     used everywhere else in this dashboard.
  5. Two radio button groups added, as requested: one for missing-slot
     interpolation on/off, one for how anomalous slots get cleaned
     (Auto / Regression / XGBoost / Off). "Auto" picks XGBoost when there's
     enough history for it to be reliable (>=50 valid history rows),
     otherwise falls back to Regression -- a simple, explainable rule
     rather than an expensive train/validation comparison on every render.
"""

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

import fl_config as cfg
from fl_data_helpers import (
    get_con, clean_data_glob, _globs_literal, get_hierarchy_cached,
    get_meter_granularity_cached, register_granularity_table,
)
from fl_kva_engine import _kva_select_expr
from fl_interp_engine import interpolate_series
from fl_optional_deps import HAS_XGBOOST, HAS_SKLEARN

if HAS_XGBOOST:
    import xgboost as xgb
if HAS_SKLEARN:
    from sklearn.linear_model import LinearRegression


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


def _load_meter_data(meter, start_date, end_date):
    """Load meter data for a date range, with KVA computed per the meter's
    own granularity (15-min vs 30-min)."""
    con = get_con()
    globs = _globs_literal(clean_data_glob())
    if globs == "[]":
        return pd.DataFrame()

    granularity_df = get_meter_granularity_cached()
    register_granularity_table(con, granularity_df)

    df = con.execute(f"""
        SELECT
            c.{cfg.COL_DATE} AS DATE,
            c.{cfg.COL_TIME_SLOT} AS TIME_SLOT,
            {_kva_select_expr('c')} AS KVA,
            c.KW_R, c.KW_Y, c.KW_B,
            c.KVAR_R, c.KVAR_Y, c.KVAR_B,
            c.VR, c.VY, c.VB,
            c.IR, c.IY, c.IB
        FROM read_parquet({globs}) c
        LEFT JOIN meter_granularity g ON c.{cfg.COL_METER} = g.MTR_NO
        WHERE c.{cfg.COL_METER} = '{meter}'
          AND c.{cfg.COL_DATE} >= DATE '{start_date}' AND c.{cfg.COL_DATE} <= DATE '{end_date}'
        ORDER BY DATE, TIME_SLOT
    """).df()

    if df.empty:
        return df
    df["YEAR"] = df["DATE"].dt.year
    df["MONTH"] = df["DATE"].dt.month

    # FIX: DuckDB returns TIME_SLOT as datetime.time objects.  Everything
    # downstream (slot grid, XGBoost s[:2] parsing, threshold joins,
    # Plotly categorical axis) expects plain "HH:MM" strings.  Convert once
    # here so every caller gets a consistent str column.
    if not df.empty and hasattr(df["TIME_SLOT"].iloc[0], "hour"):
        df["TIME_SLOT"] = df["TIME_SLOT"].apply(
            lambda t: f"{t.hour:02d}:{t.minute:02d}" if pd.notna(t) else t
        )
    return df


def _full_day_slots(df_group):
    """Full slot grid for the day, derived from whatever TIME_SLOT values
    actually appear across the loaded window -- works for both 30-min
    (48/day) and 15-min smart meters (96/day) without needing to hardcode
    either, and without needing a separate granularity lookup here.

    IMPORTANT: pass the FULL loaded window (not just the target day), so
    the slot grid reflects the meter's true daily cadence. If you pass
    only the target-day slice, a day with sparse readings would shrink
    the grid to just the slots present that day, and "missing slots"
    would silently come back as zero."""
    if df_group is not None and not df_group.empty:
        slots = sorted(df_group["TIME_SLOT"].dropna().unique().tolist())
        if slots:
            return slots
    return ["{:02d}:{:02d}".format(h, m) for h in range(24) for m in (0, 30)]


def compute_7day_threshold(df_group, param, target_date, tolerance_pct=5.0):
    """For each time slot, compute upper/lower thresholds from the last 7 days."""
    if param not in df_group.columns or "TIME_SLOT" not in df_group.columns:
        return pd.DataFrame()

    td = pd.Timestamp(target_date)
    window_start = td - pd.Timedelta(days=7)
    window_end = td - pd.Timedelta(days=1)

    hist = df_group[(df_group["DATE"] >= window_start) & (df_group["DATE"] <= window_end)]
    if hist.empty:
        return pd.DataFrame()

    tol = tolerance_pct / 100.0
    agg = hist.groupby("TIME_SLOT")[param].agg(
        HIST_MAX="max", HIST_MIN="min", HIST_MEAN="mean",
    ).reset_index()
    agg["UPPER_THRESH"] = agg["HIST_MAX"] * (1 + tol)
    agg["LOWER_THRESH"] = agg["HIST_MIN"] * (1 - tol)
    return agg.sort_values("TIME_SLOT").reset_index(drop=True)


def flag_anomalies_threshold(actual_series, thresh_df, param):
    """Flag slots where the actual value falls outside the threshold band.
    Returns a DataFrame with TIME_SLOT as a normal COLUMN (not the index) --
    callers must reference row["TIME_SLOT"], not row.index, for x-positions."""
    if thresh_df.empty or actual_series.empty:
        return pd.DataFrame()
    merged = thresh_df.set_index("TIME_SLOT").join(actual_series.rename("ACTUAL"), how="inner")
    merged["IS_ANOMALY"] = (merged["ACTUAL"] > merged["UPPER_THRESH"]) | (merged["ACTUAL"] < merged["LOWER_THRESH"])
    merged["ANOMALY_DIR"] = np.where(
        merged["ACTUAL"] > merged["UPPER_THRESH"], "HIGH",
        np.where(merged["ACTUAL"] < merged["LOWER_THRESH"], "LOW", "OK"),
    )
    return merged.reset_index()  # TIME_SLOT becomes a column here


def predict_regression_curve(df_group, param, target_date, slots, history_days=120):
    """Predict values for ALL slots using linear regression on historical data.
    This ensures both high and low threshold anomalies can be corrected."""
    if not HAS_SKLEARN or not slots:
        return pd.Series(dtype=float)
    td = pd.Timestamp(target_date)
    window_start = td - pd.Timedelta(days=history_days)
    hist = df_group[(df_group["DATE"] >= window_start) & (df_group["DATE"] < td)].copy()
    if hist.empty:
        return pd.Series(dtype=float)
    hist["ORD"] = hist["DATE"].map(lambda d: d.toordinal())
    target_ord = td.toordinal()

    preds = {}
    for s in slots:
        sub = hist[hist["TIME_SLOT"] == s][["ORD", param]].dropna()
        if len(sub) < 2:
            # Not enough history for this slot - use median of nearby slots as fallback
            nearby = hist[hist["TIME_SLOT"].notna()][param].dropna()
            if not nearby.empty:
                preds[s] = float(nearby.median())
            continue
        try:
            model = LinearRegression().fit(sub[["ORD"]].values, sub[param].values)
            pred_val = float(model.predict([[target_ord]])[0])
            preds[s] = max(0.0, pred_val)
        except Exception:
            # Fallback to median if regression fails
            nearby = hist[hist["TIME_SLOT"].notna()][param].dropna()
            if not nearby.empty:
                preds[s] = float(nearby.median())
    return pd.Series(preds).sort_index()


def predict_xgboost_curve(df_group, param, target_date, slots, history_days=180):
    """Predict values for ALL slots using XGBoost on historical data.
    Robust feature engineering with fallback to regression if XGBoost fails.
    
    IMPORTANT: XGBoost 3.x may raise specific errors on certain data patterns
    (e.g. constant target, all-zero features). These are NOT bugs — they are
    caught and logged, and we fall back gracefully to regression."""
    if not HAS_XGBOOST or not slots:
        return pd.Series(dtype=float)
    td = pd.Timestamp(target_date)
    window_start = td - pd.Timedelta(days=history_days)
    hist = df_group[(df_group["DATE"] >= window_start) & (df_group["DATE"] < td) & df_group[param].notna()].copy()
    if len(hist) < 50:
        return predict_regression_curve(df_group, param, target_date, slots, history_days)

    # Build feature matrix
    try:
        hist.loc[:, "TIME_SLOT_STR"] = hist["TIME_SLOT"].astype(str)
        hist.loc[:, "HOUR"] = hist["TIME_SLOT_STR"].str[:2].astype(int)
        hist.loc[:, "MINUTE"] = hist["TIME_SLOT_STR"].str[3:5].astype(int)
        hist.loc[:, "DOW"] = hist["DATE"].dt.dayofweek
        hist.loc[:, "MONTH"] = hist["DATE"].dt.month
        hist.loc[:, "DAY"] = hist["DATE"].dt.day
        hist.loc[:, "ORD"] = hist["DATE"].map(lambda d: d.toordinal())
        hist.loc[:, "HOUR_SIN"] = np.sin(2 * np.pi * hist["HOUR"] / 24)
        hist.loc[:, "HOUR_COS"] = np.cos(2 * np.pi * hist["HOUR"] / 24)
        hist.loc[:, "DOW_SIN"] = np.sin(2 * np.pi * hist["DOW"] / 7)
        hist.loc[:, "DOW_COS"] = np.cos(2 * np.pi * hist["DOW"] / 7)
    except Exception as fe:
        print(f"[XGBoost] Feature engineering failed: {fe}")
        return predict_regression_curve(df_group, param, target_date, slots, history_days)

    feats = ["HOUR", "MINUTE", "DOW", "MONTH", "DAY", "ORD", "HOUR_SIN", "HOUR_COS", "DOW_SIN", "DOW_COS"]

    # Validate feature matrix before model training
    X_train = hist[feats].values
    y_train = hist[param].values
    if X_train.shape[0] < 50 or X_train.shape[1] == 0:
        return predict_regression_curve(df_group, param, target_date, slots, history_days)
    # Check for constant target (XGBoost fails on zero-variance targets)
    if np.nanstd(y_train) < 1e-10:
        return predict_regression_curve(df_group, param, target_date, slots, history_days)
    # Check for NaN/Inf in features
    if np.any(np.isnan(X_train)) or np.any(np.isinf(X_train)):
        X_train = np.nan_to_num(X_train, nan=0.0, posinf=1e10, neginf=-1e10)

    try:
        model = xgb.XGBRegressor(
            n_estimators=150, max_depth=4, learning_rate=0.08,
            subsample=0.85, colsample_bytree=0.85,
            objective="reg:squarederror", verbosity=0,
            random_state=42
        )
        model.fit(X_train, y_train)
    except Exception as e:
        print(f"[XGBoost] Model training failed: {e}")
        return predict_regression_curve(df_group, param, target_date, slots, history_days)

    # Build prediction feature rows
    rows, valid_slots = [], []
    try:
        for s in slots:
            h, m = int(s[:2]), int(s[3:5])
            rows.append([
                h, m, td.dayofweek, td.month, td.day, td.toordinal(),
                np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24),
                np.sin(2 * np.pi * td.dayofweek / 7), np.cos(2 * np.pi * td.dayofweek / 7),
            ])
            valid_slots.append(s)
    except Exception as pe:
        print(f"[XGBoost] Prediction row construction failed: {pe}")
        return predict_regression_curve(df_group, param, target_date, slots, history_days)

    if not rows:
        return pd.Series(dtype=float)

    try:
        X_pred = np.array(rows, dtype=np.float64)
        preds = model.predict(X_pred)
        if np.any(np.isnan(preds)):
            print(f"[XGBoost] Predictions contain NaN — falling back to regression")
            return predict_regression_curve(df_group, param, target_date, slots, history_days)
        return pd.Series(np.maximum(preds, 0), index=valid_slots)
    except Exception as pe:
        print(f"[XGBoost] Prediction failed: {pe}")
        return predict_regression_curve(df_group, param, target_date, slots, history_days)


def _n_history_rows(df_group, param, target_date, history_days=180):
    td = pd.Timestamp(target_date)
    window_start = td - pd.Timedelta(days=history_days)
    return int(df_group[(df_group["DATE"] >= window_start) & (df_group["DATE"] < td) & df_group[param].notna()].shape[0])


def render():
    st.header("Load Curve")
    st.markdown(
        '<div class="alert at">Gap-length-aware interpolation for missing slots + a 7-day rolling '
        'threshold band to flag anomalous slots, with Regression/XGBoost overlays to estimate what '
        'those anomalous slots "should" read.</div>',
        unsafe_allow_html=True,
    )

    hierarchy = get_hierarchy_cached()
    if hierarchy is None or hierarchy.empty:
        st.warning("Hierarchy cache not available.")
        return

    c1, c2 = st.columns(2)
    with c1:
        fl = st.selectbox("FL", options=sorted(hierarchy[cfg.COL_FL].dropna().unique()), key="lc_fl")
    dt_options = sorted(hierarchy[hierarchy[cfg.COL_FL] == fl][cfg.COL_DT].dropna().unique())
    with c2:
        dt = st.selectbox("DT", options=dt_options, key="lc_dt")

    all_meter_options = sorted(hierarchy[hierarchy[cfg.COL_DT] == dt][cfg.COL_METER].dropna().unique())

    con = get_con()
    globs = _globs_literal(clean_data_glob())
    if globs != "[]":
        date_range = con.execute(f"SELECT MIN(DATE) AS MIN_D, MAX(DATE) AS MAX_D FROM read_parquet({globs})").df()
        d_min = date_range["MIN_D"].iloc[0].date() if not date_range.empty else datetime.now().date()
        d_max = date_range["MAX_D"].iloc[0].date() if not date_range.empty else datetime.now().date()
    else:
        d_min = d_max = datetime.now().date()

    picked_date = st.date_input("Select Date", value=d_max, min_value=d_min, max_value=d_max, key="lc_date")

    # ---------------------------------------------------------------
    # FIX: compute the date-filtered meter list FIRST, then render exactly
    # ONE selectbox (previously this rendered a second selectbox with the
    # SAME key as the first -- Streamlit's error on that was being silently
    # swallowed, so the filter never actually applied).
    # ---------------------------------------------------------------
    meter_options = all_meter_options
    if globs != "[]" and all_meter_options:
        try:
            active_df = con.execute(f"""
                SELECT DISTINCT {cfg.COL_METER}
                FROM read_parquet({globs})
                WHERE {cfg.COL_DT} = '{dt}' AND DATE = DATE '{picked_date}'
            """).df()
            if not active_df.empty:
                active = set(active_df[cfg.COL_METER].tolist())
                filtered = [m for m in all_meter_options if m in active]
                if filtered:
                    meter_options = filtered
        except Exception:
            pass  # fall back to the unfiltered list if this lookup fails

    if not meter_options:
        st.warning(f"No meters found with data on {picked_date} for DT {dt}.")
        return
    meter = st.selectbox(f"Meter (active on {picked_date}, {len(meter_options)} of {len(all_meter_options)} total)",
                          options=meter_options, key="lc_meter")

    param_options = ["KVA", "VR", "VY", "VB", "IR", "IY", "IB", "KW_R", "KW_Y", "KW_B", "KVAR_R", "KVAR_Y", "KVAR_B"]
    param = st.selectbox("Select Parameter", options=param_options, index=0, key="lc_param")

    # ---------------------------------------------------------------
    # Controls: tolerance slider + the two requested radio groups.
    # ---------------------------------------------------------------
    tol_pct = st.slider("Tolerance +/- %", 1, 20, 5, key="lc_tol")

    r1, r2 = st.columns(2)
    with r1:
        interp_mode = st.radio("Missing-slot handling", ["Interpolate (gap-length aware)", "Off"],
                                key="lc_interp_mode", horizontal=True)
    with r2:
        clean_options = ["Auto (best fit)", "Regression", "Off"]
        if HAS_XGBOOST:
            clean_options.insert(2, "XGBoost")
        clean_mode = st.radio("Anomalous-slot cleaning method", clean_options,
                               key="lc_clean_mode", horizontal=True)

    if not HAS_XGBOOST:
        st.caption("xgboost not installed -- Auto falls back to Regression; XGBoost option hidden.")
    if not HAS_SKLEARN:
        st.caption("scikit-learn not installed -- Regression-based cleaning unavailable.")

    # ---------------------------------------------------------------
    # Load: target day + prior 180 days in ONE query (covers both the
    # 7-day threshold window and the regression/XGBoost training lookback).
    # ---------------------------------------------------------------
    start_date = picked_date - timedelta(days=180)
    df = _load_meter_data(meter, start_date, picked_date)
    if df.empty:
        st.info("No data found for this meter in the loaded window.")
        return

    sub_d = df[df["DATE"].dt.date == picked_date]
    actual = sub_d.groupby("TIME_SLOT")[param].mean().sort_index() if not sub_d.empty else pd.Series(dtype=float)
    if actual.empty:
        st.info("No data for the selected date.")
        return

    full_slots = _full_day_slots(df)
    actual_full = actual.reindex(full_slots)  # NaN at genuinely missing slots -- this IS the raw curve with real gaps

    thresh = compute_7day_threshold(df, param, picked_date, float(tol_pct))
    flagged = flag_anomalies_threshold(actual, thresh, param)
    anom_slots = flagged.loc[flagged["IS_ANOMALY"], "TIME_SLOT"].tolist() if not flagged.empty else []

    # FIX: Detect missing slots from actual_full (reindexed with NaN) rather than checking
    # membership in actual.index. Clean_data may already include pipeline-filled readings
    # for every slot, making the old "s not in actual.index" check always return empty.
    missing_slots = actual_full[actual_full.isna()].index.tolist()

    # ---------------------------------------------------------------
    # Missing-slot interpolation (fixed: reuses the tested, correct
    # fl_interp_engine.interpolate_series instead of the old buggy
    # hand-rolled cubic branch).
    # ---------------------------------------------------------------
    filled_full = actual_full.copy()
    interp_method_full = pd.Series(index=full_slots, dtype=object)
    if interp_mode.startswith("Interpolate") and missing_slots:
        filled_full, interp_method_full = interpolate_series(actual_full)

    interpolated_slots = interp_method_full[interp_method_full.notna()].index.tolist() if not interp_method_full.empty else []

    # ---------------------------------------------------------------
    # Anomalous-slot cleaning (Regression / XGBoost / Auto / Off).
    # "Auto" = XGBoost if there's enough history to trust it, else
    # Regression -- simple, explainable, no expensive comparison needed.
    # FIX: Predict for ALL slots to ensure both high and low anomalies get corrected
    # ---------------------------------------------------------------
    reg_curve = pd.Series(dtype=float)
    xgb_curve = pd.Series(dtype=float)
    active_clean_label = None
    if clean_mode != "Off" and anom_slots:
        # Predict for ALL slots, not just anomalous ones, to ensure proper correction
        all_slots_for_prediction = full_slots
        if clean_mode == "Regression":
            reg_curve = predict_regression_curve(df, param, picked_date, all_slots_for_prediction)
            active_clean_label = "Regression"
        elif clean_mode == "XGBoost":
            xgb_curve = predict_xgboost_curve(df, param, picked_date, all_slots_for_prediction)
            active_clean_label = "XGBoost"
        else:  # Auto (best fit)
            n_hist = _n_history_rows(df, param, picked_date)
            if HAS_XGBOOST and n_hist >= 50:
                xgb_curve = predict_xgboost_curve(df, param, picked_date, all_slots_for_prediction)
                active_clean_label = "XGBoost (auto -- enough history)"
            else:
                reg_curve = predict_regression_curve(df, param, picked_date, all_slots_for_prediction)
                active_clean_label = "Regression (auto -- limited history)"
        
        # Apply corrections to the filled curve for anomalous slots
        if not reg_curve.empty:
            for slot in anom_slots:
                if slot in reg_curve.index:
                    filled_full[slot] = reg_curve[slot]
        elif not xgb_curve.empty:
            for slot in anom_slots:
                if slot in xgb_curve.index:
                    filled_full[slot] = xgb_curve[slot]

    # ---------------------------------------------------------------
    # KPI cards
    # ---------------------------------------------------------------
    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        _kpi_card("Actual Peak", f"{actual.max():.2f}", param, "#4d96ff")
    with k2:
        _kpi_card("Anomaly Slots", f"{len(anom_slots)}", f"of {len(actual)} slots", "#ff6b6b")
    with k3:
        _kpi_card("Missing Slots", f"{len(missing_slots)}", f"of {len(full_slots)} daily slots", "#ffd54f")
    with k4:
        _kpi_card("Upper Thresh", f"{thresh['UPPER_THRESH'].max():.2f}" if not thresh.empty else "--",
                   f"+{tol_pct}% of 7d max", "#2ecc71")
    with k5:
        _kpi_card("Lower Thresh", f"{thresh['LOWER_THRESH'].min():.2f}" if not thresh.empty else "--",
                   f"-{tol_pct}% of 7d min", "#a78bfa")

    st.divider()

    # ---------------------------------------------------------------
    # Chart -- FIX: both raw and filled traces now share the SAME full
    # slot-grid x-axis. Raw uses connectgaps=False so real gaps visibly
    # break the line; filled uses connectgaps=True so it reads as the
    # continuous "repaired" curve.
    # ---------------------------------------------------------------
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
                         subplot_titles=(f"Load Curve ({param})", "Slope Analysis (slot-to-slot delta)"))

    x_labels = full_slots

    if not thresh.empty:
        td_idx = thresh.set_index("TIME_SLOT").reindex(full_slots)
        fig.add_trace(go.Scatter(x=x_labels, y=td_idx["UPPER_THRESH"], mode="lines", name="Upper Threshold",
                                  line=dict(color="#ff6b6b", dash="dot", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=x_labels, y=td_idx["LOWER_THRESH"], mode="lines", name="Lower Threshold",
                                  line=dict(color="#2ecc71", dash="dot", width=1)), row=1, col=1)

    fig.add_trace(go.Scatter(x=x_labels, y=actual_full.values, mode="lines+markers", name="Actual (raw)",
                              line=dict(color="#4d96ff", width=2), marker=dict(size=5),
                              connectgaps=False), row=1, col=1)

    if interp_mode.startswith("Interpolate"):
        fig.add_trace(go.Scatter(x=x_labels, y=filled_full.values, mode="lines", name="Interpolated (clean)",
                                  line=dict(color="#2ecc71", width=2, dash="dot"),
                                  connectgaps=True), row=1, col=1)
        if interpolated_slots:
            fig.add_trace(go.Scatter(
                x=interpolated_slots, y=filled_full.reindex(interpolated_slots).values,
                mode="markers", name="Interpolated point",
                marker=dict(size=8, color="orange", symbol="circle-open", line=dict(width=2)),
            ), row=1, col=1)

    # FIX: anomaly markers now use the TIME_SLOT column, not the row index
    if not flagged.empty:
        anom_rows = flagged[flagged["IS_ANOMALY"]]
        if not anom_rows.empty:
            fig.add_trace(go.Scatter(
                x=anom_rows["TIME_SLOT"], y=anom_rows["ACTUAL"],
                mode="markers", name="Anomaly",
                marker=dict(size=12, color="red", symbol="x", line=dict(width=2)),
            ), row=1, col=1)

    if not reg_curve.empty:
        fig.add_trace(go.Scatter(x=reg_curve.index, y=reg_curve.values, mode="markers", name="Regression predicted",
                                  marker=dict(size=9, color="#ffd54f", symbol="diamond")), row=1, col=1)
    if not xgb_curve.empty:
        fig.add_trace(go.Scatter(x=xgb_curve.index, y=xgb_curve.values, mode="markers", name="XGBoost predicted",
                                  marker=dict(size=9, color="#a78bfa", symbol="star")), row=1, col=1)

    slope_source = filled_full if interp_mode.startswith("Interpolate") else actual_full
    slope = slope_source.diff()
    fig.add_trace(go.Bar(x=x_labels, y=slope.values, name="Slope", marker_color="#ff9f43"), row=2, col=1)

    fig.update_layout(height=700, hovermode="x unified",
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_xaxes(tickangle=45)
    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

    if active_clean_label:
        st.caption(f"Anomalous-slot cleaning method used: **{active_clean_label}**")

    # FIX: Missing slots summary — list every slot that had no raw reading,
    # with the gap length and interpolation method used to fill it.
    if missing_slots:
        with st.expander(f"Missing Slots Summary ({len(missing_slots)} of {len(full_slots)} slots missing)"):
            # Group consecutive missing slots to show gap lengths
            gap_info = []
            run_start = None
            run_len = 0
            for i, s in enumerate(full_slots):
                if s in missing_slots:
                    if run_start is None:
                        run_start = i
                        run_len = 1
                    else:
                        run_len += 1
                else:
                    if run_start is not None:
                        for j in range(run_len):
                            gap_info.append({
                                "Time Slot": full_slots[run_start + j],
                                "Gap Position": f"slot {j+1} of {run_len} consecutive",
                                "Gap Length": run_len,
                                "Interpolation Method": interp_method_full.get(full_slots[run_start + j], "--"),
                                "Filled Value": round(filled_full[full_slots[run_start + j]], 3) if interp_mode.startswith("Interpolate") else "--",
                            })
                        run_start = None
                        run_len = 0
            # Handle gap ending at the last slot
            if run_start is not None:
                for j in range(run_len):
                    gap_info.append({
                        "Time Slot": full_slots[run_start + j],
                        "Gap Position": f"slot {j+1} of {run_len} consecutive",
                        "Gap Length": run_len,
                        "Interpolation Method": interp_method_full.get(full_slots[run_start + j], "--"),
                        "Filled Value": round(filled_full[full_slots[run_start + j]], 3) if interp_mode.startswith("Interpolate") else "--",
                    })
            st.dataframe(pd.DataFrame(gap_info), width='stretch', hide_index=True,
                         height=min(400, max(150, len(gap_info) * 28 + 40)))

    if interpolated_slots:
        with st.expander(f"Interpolation Details ({len(interpolated_slots)} slots filled)"):
            st.dataframe(pd.DataFrame({
                "Time Slot": interpolated_slots,
                "Method": [interp_method_full[s] for s in interpolated_slots],
                "Filled Value": [round(filled_full[s], 3) for s in interpolated_slots],
            }), width='stretch', hide_index=True, height=200)

    if anom_slots:
        with st.expander(f"Anomaly Details ({len(anom_slots)} slots)"):
            anom_rows = flagged[flagged["IS_ANOMALY"]]
            detail = anom_rows[["TIME_SLOT", "ACTUAL", "UPPER_THRESH", "LOWER_THRESH", "ANOMALY_DIR", "HIST_MEAN"]].round(3)
            if not reg_curve.empty:
                detail["REGRESSION_PRED"] = detail["TIME_SLOT"].map(reg_curve).round(3)
            if not xgb_curve.empty:
                detail["XGBOOST_PRED"] = detail["TIME_SLOT"].map(xgb_curve).round(3)
            st.dataframe(detail, width='stretch', hide_index=True, height=200)
