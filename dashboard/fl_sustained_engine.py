"""
fl_sustained_engine.py -- Sustained loading computation.

Concept (per Sid): a DT is "sustained loaded" in a band (e.g. 90-100% of
rated KVA) if it accumulates >= 30 CUMULATIVE hours in that band within a
calendar month (not necessarily contiguous hours).

Rated KVA is pulled automatically per DT from the hierarchy cache
(KVA_RATING column from master) -- nothing here asks the user to enter it
manually.

Analyzed at DT level (using the DT's rated KVA as the reference), built
from meter-level readings stitched to DT level -- if a DT had its meter
replaced mid-history, we take the currently-active meter's reading per
slot (ROW_NUMBER by meter_seq descending), so a changeover day doesn't
double-count.

KNOWN LIMITATION: on the exact changeover day, if old and new meters both
reported for an overlapping slot, only the higher meter_seq (newer meter)
reading is kept -- a documented convention, not a hidden assumption.
"""

import os
import time
import streamlit as st
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import duckdb

import fl_config as cfg
from fl_data_helpers import (
    get_con, clean_data_glob, raw_data_glob, hard_anomalies_glob, threshold_anomalies_glob,
    _globs_literal, _months_signature,
    list_available_months, get_meter_granularity_cached, register_granularity_table,
    get_hierarchy_cached, _read_disk_cache_if_fresh, _write_disk_cache,
    _files_for_month,
)
from fl_kva_engine import _kva_select_expr

SUSTAINED_CACHE = os.path.join(cfg.DASH_CACHE_DIR, "sustained_loading.parquet")
SUSTAINED_RAW_CACHE = os.path.join(cfg.DASH_CACHE_DIR, "sustained_loading_raw.parquet")
SUSTAINED_MONTHLY_CACHE_DIR = os.path.join(cfg.DASH_CACHE_DIR, "sustained_loading_monthly")

# Concurrency settings for sustained loading (memory-intensive)
# Reduced from min(cpu_count, 6) to min(cpu_count-1, 4) to prevent memory
# thrashing when DuckDB workers compete for RAM.
SUSTAINED_CONCURRENCY = max(1, min(os.cpu_count() - 1 if os.cpu_count() else 2, 4))
SUSTAINED_DUCKDB_MEMORY_GB = 4


def _monthly_cache_path(ym, prefix="raw"):
    """Path to per-month sustained loading cache file."""
    os.makedirs(SUSTAINED_MONTHLY_CACHE_DIR, exist_ok=True)
    return os.path.join(SUSTAINED_MONTHLY_CACHE_DIR, f"sustained_{prefix}_{ym}.parquet")


def _band_case_expr():
    cases = []
    for label, lo, hi in cfg.LOADING_BANDS:
        if hi is None:
            cases.append(f"WHEN loading_pct >= {lo} THEN '{label}'")
        else:
            cases.append(f"WHEN loading_pct >= {lo} AND loading_pct < {hi} THEN '{label}'")
    return "CASE " + " ".join(cases) + " ELSE NULL END"


def _build_sustained_cache_raw(_months_sig):
    """Build RAW sustained loading cache with per-month disk caching.
    
    NOTE: No @st.cache_data decorator here — the function uses st.progress()
    internally, which is FORBIDDEN inside Streamlit cached functions
    (raises CachedStFunctionError). We rely on _read_disk_cache_if_fresh /
    _write_disk_cache for the actual caching layer instead.
    """
    cached = _read_disk_cache_if_fresh(SUSTAINED_RAW_CACHE, _months_sig)
    if cached is not None:
        return cached

    granularity_df = get_meter_granularity_cached()
    hierarchy_df = get_hierarchy_cached()
    if granularity_df is None or hierarchy_df is None or hierarchy_df.empty:
        return None

    if cfg.COL_KVA_RATING not in hierarchy_df.columns:
        return None
    dt_kva = (
        hierarchy_df.groupby(cfg.COL_DT, as_index=False)[cfg.COL_KVA_RATING]
        .max()
        .rename(columns={cfg.COL_KVA_RATING: "DT_KVA_RATING"})
    )

    if not os.path.exists(cfg.EARLIEST_MASTER_CACHE):
        return None
    master_df = pd.read_parquet(cfg.EARLIEST_MASTER_CACHE)
    master_df[cfg.COL_METER] = master_df[cfg.COL_METER].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

    raw_files = raw_data_glob()
    if not raw_files:
        return None

    band_expr = _band_case_expr()

    def _process_month_raw(ym, dt_kva_df, granularity_df, master_df):
        month_cache_path = _monthly_cache_path(ym, prefix="raw")
        if os.path.exists(month_cache_path):
            try:
                return pd.read_parquet(month_cache_path).to_dict('records')
            except Exception:
                pass

        worker_con = duckdb.connect(database=":memory:")
        try:
            worker_con.execute("PRAGMA threads=2")
            worker_con.execute(f"PRAGMA memory_limit='{max(1, SUSTAINED_DUCKDB_MEMORY_GB // SUSTAINED_CONCURRENCY)}GB'")
        except Exception:
            pass
        register_granularity_table(worker_con, granularity_df)
        worker_con.register("dt_kva_df", dt_kva_df)
        worker_con.register("master_df", master_df)

        month_raw_files = _files_for_month(raw_files, ym)
        if not month_raw_files:
            worker_con.close()
            return []
        month_raw_globs = _globs_literal(month_raw_files)

        try:
            df = worker_con.execute(f"""
                WITH with_dt AS (
                    SELECT
                        r.MTR_NO, m.{cfg.COL_DT} AS DT_CODE_NEW,
                        r.OCCUR_D_RAW AS DATE,
                        CAST(strftime(r.OCCUR_D_RAW, '%Y') AS INTEGER) AS YEAR,
                        CAST(strftime(r.OCCUR_D_RAW, '%Y%m') AS INTEGER) AS YM,
                        r.TIME_SLOT, g.SLOT_MINUTES,
                        {_kva_select_expr('r')} AS KVA
                    FROM read_parquet({month_raw_globs}) r
                    LEFT JOIN master_df m ON r.MTR_NO = m.{cfg.COL_METER}
                    LEFT JOIN meter_granularity g ON r.MTR_NO = g.MTR_NO
                    WHERE m.{cfg.COL_DT} IS NOT NULL
                ),
                dt_stitched AS (
                    SELECT DT_CODE_NEW, DATE, YEAR, YM, TIME_SLOT, SLOT_MINUTES, SUM(KVA) AS KVA
                    FROM with_dt
                    GROUP BY DT_CODE_NEW, DATE, YEAR, YM, TIME_SLOT, SLOT_MINUTES
                ),
                with_loading AS (
                    SELECT s.*, k.DT_KVA_RATING,
                           s.KVA / NULLIF(k.DT_KVA_RATING, 0) AS loading_pct,
                           (s.SLOT_MINUTES / 60.0) AS slot_hours
                    FROM dt_stitched s JOIN dt_kva_df k ON s.DT_CODE_NEW = k.{cfg.COL_DT}
                ),
                banded AS (
                    SELECT *, {band_expr} AS BAND
                    FROM with_loading WHERE loading_pct IS NOT NULL AND loading_pct >= 0.70
                )
                SELECT DT_CODE_NEW, YEAR, YM, DATE, BAND, SUM(slot_hours) AS hours_in_band
                FROM banded WHERE BAND IS NOT NULL
                GROUP BY DT_CODE_NEW, YEAR, YM, DATE, BAND ORDER BY DT_CODE_NEW, DATE
            """).df()
            worker_con.close()
            if not df.empty:
                try:
                    df.to_parquet(month_cache_path, index=False)
                except Exception:
                    pass
            return df.to_dict('records')
        except Exception as e:
            worker_con.close()
            print(f"[Sustained] Error _process_month_raw({ym}): {e}")
            return []

    months = list_available_months()
    all_rows = []
    print(f"[Sustained] Building RAW cache for {len(months)} months...")
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=SUSTAINED_CONCURRENCY) as month_pool:
        futures = {month_pool.submit(_process_month_raw, ym, dt_kva, granularity_df, master_df): ym for ym in months}
        for future in as_completed(futures):
            ym = futures[future]
            month_rows = future.result()
            all_rows.extend(month_rows)

    elapsed = time.time() - t_start
    print(f"[Sustained] RAW cache built in {elapsed:.0f}s for {len(months)} months")

    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(
        columns=["DT_CODE_NEW", "YEAR", "YM", "DATE", "BAND", "hours_in_band"]
    )

    os.makedirs(cfg.DASH_CACHE_DIR, exist_ok=True)
    _write_disk_cache(SUSTAINED_RAW_CACHE, _months_sig, df)
    return df


def _build_sustained_cache_clean(_months_sig):
    """Build CLEAN sustained loading cache with per-month disk caching.
    
    NOTE: No @st.cache_data decorator — this function computes heavy SQL
    and uses per-month disk caching for incremental builds. The disk cache
    layer (_read_disk_cache_if_fresh / _write_disk_cache) handles all
    caching concerns. Progress is reported via print() to avoid
    CachedStFunctionError from Streamlit widgets inside cache functions.
    """
    cached = _read_disk_cache_if_fresh(SUSTAINED_CACHE, _months_sig)
    if cached is not None:
        return cached

    granularity_df = get_meter_granularity_cached()
    hierarchy_df = get_hierarchy_cached()
    if granularity_df is None or hierarchy_df is None or hierarchy_df.empty:
        return None

    if cfg.COL_KVA_RATING not in hierarchy_df.columns:
        return None
    dt_kva = (
        hierarchy_df.groupby(cfg.COL_DT, as_index=False)[cfg.COL_KVA_RATING]
        .max()
        .rename(columns={cfg.COL_KVA_RATING: "DT_KVA_RATING"})
    )

    clean_files = clean_data_glob()
    hard_files = hard_anomalies_glob()
    thresh_files = threshold_anomalies_glob()
    if not clean_files:
        return None

    band_expr = _band_case_expr()

    def _process_month_clean(ym, dt_kva_df, granularity_df):
        month_cache_path = _monthly_cache_path(ym, prefix="clean")
        if os.path.exists(month_cache_path):
            try:
                return pd.read_parquet(month_cache_path).to_dict('records')
            except Exception:
                pass

        worker_con = duckdb.connect(database=":memory:")
        try:
            worker_con.execute("PRAGMA threads=2")
            worker_con.execute(f"PRAGMA memory_limit='{max(1, SUSTAINED_DUCKDB_MEMORY_GB // SUSTAINED_CONCURRENCY)}GB'")
        except Exception:
            pass
        register_granularity_table(worker_con, granularity_df)
        worker_con.register("dt_kva_df", dt_kva_df)

        month_clean_files = _files_for_month(clean_files, ym)
        if not month_clean_files:
            worker_con.close()
            return []
        month_clean_globs = _globs_literal(month_clean_files)

        bad_parts = []
        month_hard_files = _files_for_month(hard_files, ym)
        month_thresh_files = _files_for_month(thresh_files, ym)
        if month_hard_files:
            bad_parts.append(f"SELECT MTR_NO, DATE, TIME_SLOT FROM read_parquet({_globs_literal(month_hard_files)})")
        if month_thresh_files:
            bad_parts.append(f"SELECT MTR_NO, DATE, TIME_SLOT FROM read_parquet({_globs_literal(month_thresh_files)})")
        bad_sql = (" UNION ".join(bad_parts) if bad_parts
                   else "SELECT NULL AS MTR_NO, NULL AS DATE, NULL AS TIME_SLOT WHERE FALSE")

        try:
            df = worker_con.execute(f"""
                WITH bad AS ({bad_sql}),
                dt_stitched AS (
                    SELECT * FROM (
                        SELECT
                            c.{cfg.COL_DT} AS DT_CODE_NEW, c.{cfg.COL_DATE} AS DATE,
                            CAST(strftime(c.{cfg.COL_DATE}, '%Y') AS INTEGER) AS YEAR,
                            CAST(strftime(c.{cfg.COL_DATE}, '%Y%m') AS INTEGER) AS YM,
                            g.SLOT_MINUTES,
                            CASE WHEN b.MTR_NO IS NOT NULL THEN NULL ELSE {_kva_select_expr('c')} END AS KVA,
                            ROW_NUMBER() OVER (
                                PARTITION BY c.{cfg.COL_DT}, c.{cfg.COL_DATE}, c.{cfg.COL_TIME_SLOT}
                                ORDER BY COALESCE(c.{cfg.COL_METER_SEQ}, 1) DESC
                            ) AS rn
                        FROM read_parquet({month_clean_globs}) c
                        LEFT JOIN meter_granularity g ON c.{cfg.COL_METER} = g.MTR_NO
                        LEFT JOIN bad b
                            ON c.{cfg.COL_METER} = b.MTR_NO AND c.{cfg.COL_DATE} = b.DATE AND c.{cfg.COL_TIME_SLOT} = b.TIME_SLOT
                        WHERE c.{cfg.COL_DT} IS NOT NULL
                    ) WHERE rn = 1
                ),
                with_loading AS (
                    SELECT s.*, k.DT_KVA_RATING,
                           s.KVA / NULLIF(k.DT_KVA_RATING, 0) AS loading_pct,
                           (s.SLOT_MINUTES / 60.0) AS slot_hours
                    FROM dt_stitched s JOIN dt_kva_df k ON s.DT_CODE_NEW = k.{cfg.COL_DT}
                ),
                banded AS (
                    SELECT *, {band_expr} AS BAND
                    FROM with_loading WHERE loading_pct IS NOT NULL AND loading_pct >= 0.70
                )
                SELECT DT_CODE_NEW, YEAR, YM, DATE, BAND, SUM(slot_hours) AS hours_in_band
                FROM banded WHERE BAND IS NOT NULL
                GROUP BY DT_CODE_NEW, YEAR, YM, DATE, BAND ORDER BY DT_CODE_NEW, DATE
            """).df()
            worker_con.close()
            if not df.empty:
                try:
                    df.to_parquet(month_cache_path, index=False)
                except Exception:
                    pass
            return df.to_dict('records')
        except Exception as e:
            worker_con.close()
            print(f"[Sustained] Error _process_month_clean({ym}): {e}")
            return []

    months = list_available_months()
    all_rows = []
    print(f"[Sustained] Building CLEAN cache for {len(months)} months...")
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=SUSTAINED_CONCURRENCY) as month_pool:
        futures = {month_pool.submit(_process_month_clean, ym, dt_kva, granularity_df): ym for ym in months}
        for future in as_completed(futures):
            ym = futures[future]
            month_rows = future.result()
            all_rows.extend(month_rows)

    elapsed = time.time() - t_start
    print(f"[Sustained] CLEAN cache built in {elapsed:.0f}s for {len(months)} months")

    df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(
        columns=["DT_CODE_NEW", "YEAR", "YM", "DATE", "BAND", "hours_in_band"]
    )

    os.makedirs(cfg.DASH_CACHE_DIR, exist_ok=True)
    _write_disk_cache(SUSTAINED_CACHE, _months_sig, df)
    return df


def get_sustained_cache_raw():
    """Returns RAW sustained loading cache (fast, SQL aggregation only)."""
    months = list_available_months()
    return _build_sustained_cache_raw(_months_signature(months))


def get_sustained_cache_clean():
    """Returns CLEAN sustained loading cache (fast SQL aggregation with anomaly filtering)."""
    months = list_available_months()
    return _build_sustained_cache_clean(_months_signature(months))


def monthly_band_hours(daily_df):
    """Roll the daily band-hours cache up to (DT, YM, BAND) totals."""
    if daily_df is None or daily_df.empty:
        return pd.DataFrame()
    return (
        daily_df.groupby(["DT_CODE_NEW", "YEAR", "YM", "BAND"], as_index=False)["hours_in_band"]
        .sum()
    )
