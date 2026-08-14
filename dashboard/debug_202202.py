"""
debug_202202.py -- Standalone diagnostic for the CLEAN sustained-loading
build hanging on month 202202.

Run this FROM THE dashboard FOLDER on the server:
    cd "D:\\New dashboard code\\DT DATA DASHBOARD V1\\DT-LOADING-BRPL-NETWORK\\dashboard"
    python debug_202202.py

It reproduces _process_month_clean's logic for a single month, but with a
print() + flush after every stage, and a few EXPLAIN calls, so we can see
exactly which step never returns.
"""

import sys
import time
import duckdb
import pandas as pd

import fl_config as cfg
from fl_data_helpers import (
    clean_data_glob, hard_anomalies_glob, threshold_anomalies_glob,
    _globs_literal, get_meter_granularity_cached, register_granularity_table,
    get_hierarchy_cached, _files_for_month,
)
from fl_kva_engine import _kva_select_expr
from fl_sustained_engine import _band_case_expr

YM = "202202"  # the month that hangs -- must be a STRING to match list_available_months()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    log("Starting standalone debug for month %s" % YM)

    log("Loading granularity cache...")
    granularity_df = get_meter_granularity_cached()
    log(f"  -> granularity_df: {None if granularity_df is None else len(granularity_df)} rows")

    log("Loading hierarchy cache...")
    hierarchy_df = get_hierarchy_cached()
    log(f"  -> hierarchy_df: {None if hierarchy_df is None else len(hierarchy_df)} rows")

    if hierarchy_df is None or hierarchy_df.empty:
        log("Hierarchy empty/missing -- aborting")
        sys.exit(1)

    dt_kva = (
        hierarchy_df.groupby(cfg.COL_DT, as_index=False)[cfg.COL_KVA_RATING]
        .max()
        .rename(columns={cfg.COL_KVA_RATING: "DT_KVA_RATING"})
    )
    log(f"  -> dt_kva: {len(dt_kva)} rows")

    clean_files = clean_data_glob()
    hard_files = hard_anomalies_glob()
    thresh_files = threshold_anomalies_glob()
    log(f"clean_files total: {len(clean_files)} | hard: {len(hard_files)} | thresh: {len(thresh_files)}")

    month_clean_files = _files_for_month(clean_files, YM)
    month_hard_files = _files_for_month(hard_files, YM)
    month_thresh_files = _files_for_month(thresh_files, YM)
    log(f"For {YM}: clean={len(month_clean_files)} hard={len(month_hard_files)} thresh={len(month_thresh_files)}")
    log(f"  clean files: {month_clean_files}")
    log(f"  hard files: {month_hard_files}")
    log(f"  thresh files: {month_thresh_files}")

    if not month_clean_files:
        log("No clean files for this month -- nothing to do")
        sys.exit(0)

    month_clean_globs = _globs_literal(month_clean_files)

    log("Connecting to DuckDB (single-threaded, in-memory)...")
    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA threads=1")
    con.execute("PRAGMA memory_limit='8GB'")
    log("  -> connected")

    log("Registering granularity + dt_kva tables...")
    register_granularity_table(con, granularity_df)
    con.register("dt_kva_df", dt_kva)
    log("  -> registered")

    bad_parts = []
    if month_hard_files:
        bad_parts.append(f"SELECT MTR_NO, DATE, TIME_SLOT FROM read_parquet({_globs_literal(month_hard_files)})")
    if month_thresh_files:
        bad_parts.append(f"SELECT MTR_NO, DATE, TIME_SLOT FROM read_parquet({_globs_literal(month_thresh_files)})")
    bad_sql = (" UNION ".join(bad_parts) if bad_parts
               else "SELECT NULL AS MTR_NO, NULL AS DATE, NULL AS TIME_SLOT WHERE FALSE")

    # ---- Stage 1: just count rows in the raw clean_data scan for this month ----
    log("STAGE 1: counting raw rows in clean_data for this month...")
    t0 = time.time()
    try:
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet({month_clean_globs})").fetchone()[0]
        log(f"  -> {n} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"  !! STAGE 1 FAILED: {e}")
        sys.exit(1)

    # ---- Stage 2: count rows in the "bad" (anomaly) union ----
    log("STAGE 2: counting anomaly (bad) rows for this month...")
    t0 = time.time()
    try:
        n = con.execute(f"SELECT COUNT(*) FROM ({bad_sql}) t").fetchone()[0]
        log(f"  -> {n} anomaly rows in {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"  !! STAGE 2 FAILED: {e}")
        sys.exit(1)

    # ---- Stage 3: the join to master/granularity (no window function yet) ----
    log("STAGE 3: join clean_data to granularity (no window fn), counting...")
    t0 = time.time()
    try:
        n = con.execute(f"""
            SELECT COUNT(*) FROM read_parquet({month_clean_globs}) c
            LEFT JOIN meter_granularity g ON c.{cfg.COL_METER} = g.MTR_NO
            WHERE c.{cfg.COL_DT} IS NOT NULL
        """).fetchone()[0]
        log(f"  -> {n} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"  !! STAGE 3 FAILED: {e}")
        sys.exit(1)

    # ---- Stage 4: add the LEFT JOIN to bad (this is often the expensive one) ----
    log("STAGE 4: join clean_data to granularity + bad anomalies, counting...")
    t0 = time.time()
    try:
        n = con.execute(f"""
            WITH bad AS ({bad_sql})
            SELECT COUNT(*) FROM read_parquet({month_clean_globs}) c
            LEFT JOIN meter_granularity g ON c.{cfg.COL_METER} = g.MTR_NO
            LEFT JOIN bad b
                ON c.{cfg.COL_METER} = b.MTR_NO AND c.{cfg.COL_DATE} = b.DATE AND c.{cfg.COL_TIME_SLOT} = b.TIME_SLOT
            WHERE c.{cfg.COL_DT} IS NOT NULL
        """).fetchone()[0]
        log(f"  -> {n} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"  !! STAGE 4 FAILED: {e}")
        sys.exit(1)

    # ---- Stage 5: add the ROW_NUMBER window function (suspect #1 for hangs) ----
    log("STAGE 5: same as stage 4 + ROW_NUMBER() window function, counting...")
    t0 = time.time()
    try:
        n = con.execute(f"""
            WITH bad AS ({bad_sql}),
            windowed AS (
                SELECT
                    c.{cfg.COL_DT} AS DT_CODE_NEW,
                    ROW_NUMBER() OVER (
                        PARTITION BY c.{cfg.COL_DT}, c.{cfg.COL_DATE}, c.{cfg.COL_TIME_SLOT}
                        ORDER BY COALESCE(c.{cfg.COL_METER_SEQ}, 1) DESC
                    ) AS rn
                FROM read_parquet({month_clean_globs}) c
                LEFT JOIN meter_granularity g ON c.{cfg.COL_METER} = g.MTR_NO
                LEFT JOIN bad b
                    ON c.{cfg.COL_METER} = b.MTR_NO AND c.{cfg.COL_DATE} = b.DATE AND c.{cfg.COL_TIME_SLOT} = b.TIME_SLOT
                WHERE c.{cfg.COL_DT} IS NOT NULL
            )
            SELECT COUNT(*) FROM windowed WHERE rn = 1
        """).fetchone()[0]
        log(f"  -> {n} rows in {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"  !! STAGE 5 FAILED: {e}")
        sys.exit(1)

    log("ALL STAGES COMPLETED without hanging -- the full query should work too.")
    con.close()

if __name__ == "__main__":
    main()
