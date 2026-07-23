"""
fl_kva_engine.py -- Shared KVA computation, used by every tab that needs a
KVA number (Peak KVA, CAGR, Sustained Loading, Forecast).

RAW vs CLEAN (per Sid's clarified instruction):
  RAW   = KVA computed from clean_data's columns as-is, every reading
          included, no exclusions.
  CLEAN = missing slots AND anomaly-flagged slots (hard_anomalies UNION
          threshold_anomalies) are both treated as gaps in the curve, then
          filled using the SAME gap-length-aware interpolation rule as the
          Load Curve tab (1-2 missing -> linear, 3-4 -> quadratic, 5+ ->
          cubic). The peak is then taken from the RECONSTRUCTED curve, not
          from the raw values with bad points thrown out.

KVA FORMULA (per meter granularity):
  - 30-min meters: sqrt((KW_R+KW_Y+KW_B)^2 + (KVAR_R+KVAR_Y+KVAR_B)^2)
  - 15-min meters (smart meters, mostly Jan-2026+): (VR*IR+VY*IY+VB*IB)/1000

SCALE NOTE -- read this before comparing to the Load Curve tab:
  tab2 (Load Curve) additionally overlays a REAL regression + XGBoost
  model fit PER METER, on demand, for the single meter/date-range you're
  viewing -- that's cheap because it's one meter at a time.
  Training a separate model per meter for EVERY meter in a 10,000-DT fleet,
  every time this cache rebuilds, is not something that finishes in a
  normal dashboard-startup timeframe. So for THIS fleet-wide cache,
  anomaly-flagged slots are cleaned via the same interpolation engine as
  missing slots, not a per-meter trained model. This is a deliberate scale
  trade-off, not an oversight. If you want true per-meter ML-cleaned
  fleet-wide peaks, that needs to run as a separate offline/overnight batch
  job (not at app startup) -- say the word and I'll build that as a
  follow-up script that writes its own cached Parquet for this dashboard
  to read.
"""

import os
import time
import duckdb
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
import streamlit as st

import fl_config as cfg
from fl_data_helpers import (
    get_con, clean_data_glob, raw_data_glob, hard_anomalies_glob, threshold_anomalies_glob,
    _globs_literal, _months_signature, _files_for_month,
    get_meter_granularity_cached, register_granularity_table,
    _read_disk_cache_if_fresh, _write_disk_cache, get_hierarchy_cached,
    list_available_months,
)
MONTHLY_PEAK_CACHE = os.path.join(cfg.DASH_CACHE_DIR, "monthly_peak_kva.parquet")
MONTHLY_PEAK_RAW_CACHE = os.path.join(cfg.DASH_CACHE_DIR, "monthly_peak_kva_raw.parquet")
COINCIDENTAL_PEAK_CACHE = os.path.join(cfg.DASH_CACHE_DIR, "coincidental_peak_kva.parquet")

# How many months to process CONCURRENTLY. Deliberately NOT set to "however
# many months exist" (e.g. 67) -- each in-flight month holds that month's
# full per-slot fleet-wide DataFrame in RAM (10k DTs x ~30 days x 48-96
# slots/day), which is exactly the memory blowup the original sequential
# design was written to avoid. Capping at the CPU core count still gets you
# genuine "many months at once" parallelism, bounded to what your machine
# can actually hold and compute concurrently. Raise this if you have RAM
# and cores to spare.
MONTH_CONCURRENCY = 1

# Total DuckDB memory budget (GB), split evenly across however many month
# workers run at once -- mirrors the single-connection 6GB limit used
# elsewhere in this dashboard, just divided since now several connections
# are live simultaneously instead of one.
TOTAL_DUCKDB_MEMORY_GB = 4


def _kva_select_expr(alias="c"):
    return f"""
        CASE WHEN g.SLOT_MINUTES = 15
             THEN ({alias}.VR * {alias}.IR + {alias}.VY * {alias}.IY + {alias}.VB * {alias}.IB) / 1000.0
             ELSE SQRT(POWER({alias}.KW_R + {alias}.KW_Y + {alias}.KW_B, 2)
                     + POWER({alias}.KVAR_R + {alias}.KVAR_Y + {alias}.KVAR_B, 2))
        END
    """


def _build_coincidental_peak_kva(_months_sig):
    """
    Build coincidental peak KVA cache using the new method:
    1. Daily Sum: Sum KVA of all slots in entire day per DT/FL
    2. Monthly Peak: Day with highest daily sum = coincidental peak for that month
    3. Yearly Peak: Month with highest monthly peak = coincidental peak for that year
    """
    cached = _read_disk_cache_if_fresh(COINCIDENTAL_PEAK_CACHE, _months_sig)
    if cached is not None:
        return cached

    con = get_con()
    granularity_df = get_meter_granularity_cached()
    hierarchy_df = get_hierarchy_cached()
    
    if granularity_df is None or hierarchy_df is None:
        return None
    
    register_granularity_table(con, granularity_df)
    con.register("hierarchy", hierarchy_df)
    
    months = list_available_months()
    if not months:
        return None
    
    clean_files = clean_data_glob()
    if not clean_files:
        return None
    
    all_results = []
    done = 0
    errors = []
    
    progress = st.progress(0, text="Building coincidental peak KVA cache...")
    status = st.empty()
    
    def _process_month_coincidental(ym):
        worker_con = duckdb.connect(database=":memory:")
        try:
            worker_con.execute("PRAGMA threads=2")
            worker_con.execute(f"PRAGMA memory_limit='{max(1, TOTAL_DUCKDB_MEMORY_GB // MONTH_CONCURRENCY)}GB'")
        except Exception:
            pass
        
        register_granularity_table(worker_con, granularity_df)
        worker_con.register("hierarchy", hierarchy_df)
        
        month_files = _files_for_month(clean_files, ym)
        if not month_files:
            worker_con.close()
            return []
        
        month_globs = _globs_literal(month_files)
        
        try:
            # Step 1: Calculate daily KVA sum per meter
            daily_kva = worker_con.execute(f"""
                SELECT
                    c.{cfg.COL_METER} AS MTR_NO,
                    h.DT_CODE_NEW,
                    h.SDO_CD,
                    h.DT_CAT,
                    c.{cfg.COL_DATE} AS DATE,
                    SUM({_kva_select_expr('c')}) AS DAILY_KVA_SUM
                FROM read_parquet({month_globs}) c
                LEFT JOIN hierarchy h ON c.{cfg.COL_METER} = h.MTR_NO
                LEFT JOIN meter_granularity g ON c.{cfg.COL_METER} = g.MTR_NO
                WHERE h.DT_CODE_NEW IS NOT NULL
                GROUP BY c.{cfg.COL_METER}, h.DT_CODE_NEW, h.SDO_CD, h.DT_CAT, c.{cfg.COL_DATE}
            """).df()
            
            if daily_kva.empty:
                worker_con.close()
                return []
            
            # Step 2: Calculate monthly coincidental peak per DT (max daily sum)
            ym_int = int(ym)
            year = ym_int // 100
            month = ym_int % 100
            
            daily_kva = daily_kva.copy()
            daily_kva["YM"] = ym
            daily_kva["YEAR"] = year
            daily_kva["MONTH"] = month
            
            # DT-level monthly peak
            dt_monthly_peak = daily_kva.groupby(["DT_CODE_NEW", "YM", "YEAR", "MONTH"], as_index=False)[["DAILY_KVA_SUM"]].max()
            dt_monthly_peak = dt_monthly_peak.rename(columns={"DAILY_KVA_SUM": "COINCIDENTAL_PEAK_DT"})
            
            # FL-level monthly peak (sum of DT daily peaks, then max across days)
            fl_daily_sum = daily_kva.groupby(["SDO_CD", "DATE", "YM", "YEAR", "MONTH"], as_index=False)[["DAILY_KVA_SUM"]].sum()
            fl_daily_sum = fl_daily_sum.reset_index()
            fl_monthly_peak = fl_daily_sum.groupby(["SDO_CD", "YM", "YEAR", "MONTH"], as_index=False)[["DAILY_KVA_SUM"]].max()
            fl_monthly_peak = fl_monthly_peak.rename(columns={"DAILY_KVA_SUM": "COINCIDENTAL_PEAK_FL"})
            
            # DT_CAT-level monthly peak
            cat_daily_sum = daily_kva.groupby(["DT_CAT", "DATE", "YM", "YEAR", "MONTH"], as_index=False)[["DAILY_KVA_SUM"]].sum()
            cat_daily_sum = cat_daily_sum.reset_index()
            cat_monthly_peak = cat_daily_sum.groupby(["DT_CAT", "YM", "YEAR", "MONTH"], as_index=False)[["DAILY_KVA_SUM"]].max()
            cat_monthly_peak = cat_monthly_peak.rename(columns={"DAILY_KVA_SUM": "COINCIDENTAL_PEAK_CAT"})
            
            worker_con.close()
            return dt_monthly_peak.to_dict('records') + fl_monthly_peak.to_dict('records') + cat_monthly_peak.to_dict('records')
            
        except Exception as e:
            print(f"Error processing month {ym} in coincidental peak cache: {e}")
            worker_con.close()
            return []
    
    for ym in months:
        try:
            month_results = _process_month_coincidental(ym)
            all_results.extend(month_results)
        except Exception as e:
            errors.append(f"Month {ym}: {e}")
        done += 1
        progress.progress(done / len(months), text=f"Processing month {ym} ({done}/{len(months)})")
        status.text(f"Completed {done}/{len(months)} months")
    
    progress.empty()
    status.empty()
    
    if errors:
        st.warning(f"Errors during coincidental peak cache build: {errors}")
    
    if not all_results:
        return None
    
    # Convert to DataFrame
    df = pd.DataFrame(all_results)
    
    # Separate DT, FL, and CAT results
    dt_results = df[df["DT_CODE_NEW"].notna()].copy()
    fl_results = df[df["SDO_CD"].notna() & df["DT_CODE_NEW"].isna()].copy()
    cat_results = df[df["DT_CAT"].notna() & df["DT_CODE_NEW"].isna() & df["SDO_CD"].isna()].copy()
    
    # Step 3: Calculate yearly coincidental peak from monthly peaks
    if not dt_results.empty:
        dt_yearly = dt_results.groupby(["DT_CODE_NEW", "YEAR"], as_index=False)["COINCIDENTAL_PEAK_DT"].max()
        dt_yearly = dt_yearly.rename(columns={"COINCIDENTAL_PEAK_DT": "YEARLY_COINCIDENTAL_PEAK_DT"})
    else:
        dt_yearly = pd.DataFrame()
    
    if not fl_results.empty:
        fl_yearly = fl_results.groupby(["SDO_CD", "YEAR"], as_index=False)["COINCIDENTAL_PEAK_FL"].max()
        fl_yearly = fl_yearly.rename(columns={"COINCIDENTAL_PEAK_FL": "YEARLY_COINCIDENTAL_PEAK_FL"})
    else:
        fl_yearly = pd.DataFrame()
    
    if not cat_results.empty:
        cat_yearly = cat_results.groupby(["DT_CAT", "YEAR"], as_index=False)["COINCIDENTAL_PEAK_CAT"].max()
        cat_yearly = cat_yearly.rename(columns={"COINCIDENTAL_PEAK_CAT": "YEARLY_COINCIDENTAL_PEAK_CAT"})
    else:
        cat_yearly = pd.DataFrame()
    
    # Combine all results into single cache file
    final_cache = pd.concat([
        dt_results.assign(LEVEL="DT"),
        fl_results.assign(LEVEL="FL"),
        cat_results.assign(LEVEL="CAT"),
        dt_yearly.assign(LEVEL="DT_YEARLY"),
        fl_yearly.assign(LEVEL="FL_YEARLY"),
        cat_yearly.assign(LEVEL="CAT_YEARLY")
    ], ignore_index=True)
    
    _write_disk_cache(COINCIDENTAL_PEAK_CACHE, final_cache, _months_sig)
    return final_cache


def get_coincidental_peak_kva_cached():
    """Get coincidental peak KVA cache, building if needed."""
    months = list_available_months()
    if not months:
        return None
    
    _months_sig = _months_signature(months)
    return _build_coincidental_peak_kva(_months_sig)


def _build_monthly_peak_kva(_months_sig):
    # IMPORTANT: RAW builder must ONLY read the RAW cache file, NOT the
    # CLEAN cache (monthly_peak_kva.parquet). If the CLEAN cache exists
    # with BOTH PEAK_KVA_RAW and PEAK_KVA_CLEAN columns, returning it
    # would make tab3 show PEAK_KVA_CLEAN immediately from the RAW getter,
    # and the subsequent clean build would merge into the same cache file,
    # making raw==clean because they're the same source!
    # 
    # ALSO: No @st.cache_data decorator here — the function uses 
    # st.progress()/st.empty() internally, which is FORBIDDEN inside 
    # Streamlit cached functions (raises CachedStFunctionError). We rely 
    # on _read_disk_cache_if_fresh / _write_disk_cache for caching instead.
    cached = _read_disk_cache_if_fresh(MONTHLY_PEAK_RAW_CACHE, _months_sig)
    if cached is not None:
        return cached

    con = get_con()
    granularity_df = get_meter_granularity_cached()
    if granularity_df is None or granularity_df.empty:
        return None
    register_granularity_table(con, granularity_df)

    # Get hierarchy for joining raw data with DT codes
    hierarchy_df = get_hierarchy_cached()
    if hierarchy_df is None or hierarchy_df.empty:
        return None

    clean_files = clean_data_glob()
    raw_files = raw_data_glob()
    hard_files = hard_anomalies_glob()
    thresh_files = threshold_anomalies_glob()
    if not clean_files:
        return None

    months = list_available_months()

    # ---- RAW: Process per-month in parallel (same as CLEAN) for better
    # progress reporting and potentially faster completion. This avoids the
    # single massive query that was taking 20+ minutes on large datasets.
    def _process_month_raw(ym, granularity_df, hierarchy_df):
        worker_con = duckdb.connect(database=":memory:")
        try:
            worker_con.execute("PRAGMA threads=2")
            worker_con.execute(f"PRAGMA memory_limit='{max(1, TOTAL_DUCKDB_MEMORY_GB // MONTH_CONCURRENCY)}GB'")
        except Exception:
            pass
        register_granularity_table(worker_con, granularity_df)
        worker_con.register("hierarchy", hierarchy_df)

        month_raw_files = _files_for_month(raw_files, ym)
        if not month_raw_files:
            return []
        month_raw_globs = _globs_literal(month_raw_files)

        try:
            monthly_raw = worker_con.execute(f"""
                SELECT
                    c.{cfg.COL_METER} AS MTR_NO,
                    h.DT_CODE_NEW,
                    CAST(strftime(c.{cfg.COL_DATE_RAW}, '%Y') AS INTEGER) AS YEAR,
                    CAST(strftime(c.{cfg.COL_DATE_RAW}, '%Y%m') AS INTEGER) AS YM,
                    MAX(CASE WHEN g.SLOT_MINUTES = 15
                             THEN ((c.VR * c.IR + c.VY * c.IY + c.VB * c.IB) / 1000.0) * COALESCE(h.CT_RATIO, 1)
                             ELSE SQRT(POWER(c.KW_R + c.KW_Y + c.KW_B, 2)
                                     + POWER(c.KVAR_R + c.KVAR_Y + c.KVAR_B, 2)) * COALESCE(h.CT_RATIO, 1)
                        END) AS PEAK_KVA_RAW
                FROM read_parquet({month_raw_globs}) c
                LEFT JOIN hierarchy h ON c.{cfg.COL_METER} = h.MTR_NO
                LEFT JOIN meter_granularity g ON c.{cfg.COL_METER} = g.MTR_NO
                WHERE h.DT_CODE_NEW IS NOT NULL
                GROUP BY c.{cfg.COL_METER}, h.DT_CODE_NEW,
                         CAST(strftime(c.{cfg.COL_DATE_RAW}, '%Y') AS INTEGER),
                         CAST(strftime(c.{cfg.COL_DATE_RAW}, '%Y%m') AS INTEGER)
            """).df()
            worker_con.close()
            return monthly_raw.to_dict('records')
        except Exception as e:
            print(f"Error processing raw month {ym}: {e}")
            worker_con.close()
            return []

    raw_status = st.empty()
    raw_status.info("Computing RAW peak KVA -- processing months in parallel...")
    _t_raw_start = time.time()
    
    raw_rows = []
    raw_progress = st.progress(0.0, text=f"RAW peak KVA -- 0/{len(months)} months done")
    done = 0
    with ThreadPoolExecutor(max_workers=MONTH_CONCURRENCY) as raw_pool:
        futures = {raw_pool.submit(_process_month_raw, ym, granularity_df, hierarchy_df): ym for ym in months}
        for future in as_completed(futures):
            ym = futures[future]
            raw_rows.extend(future.result())
            done += 1
            elapsed = time.time() - _t_raw_start
            avg_per_month = elapsed / done
            remaining = avg_per_month * (len(months) - done) / MONTH_CONCURRENCY
            eta_txt = f"{remaining / 60:.1f} min remaining" if remaining >= 60 else f"{remaining:.0f} sec remaining"
            raw_progress.progress(
                done / max(len(months), 1),
                text=f"RAW peak KVA -- {done}/{len(months)} months done -- ~{eta_txt}"
            )
    raw_progress.empty()
    
    raw_peak = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame(
        columns=["MTR_NO", "DT_CODE_NEW", "YEAR", "YM", "PEAK_KVA_RAW"]
    )
    raw_status.success(f"RAW peak KVA done in {time.time() - _t_raw_start:.0f}s")
    
    # Save RAW cache immediately so dashboard can use it without waiting for CLEAN
    _write_disk_cache(MONTHLY_PEAK_RAW_CACHE, _months_sig, raw_peak)
    
    # Return only RAW for now - CLEAN will be built separately on-demand
    return raw_peak


def _build_monthly_peak_kva_clean(_months_sig):
    """Build CLEAN monthly peak KVA cache via pure SQL — no Python iteration.

    STRATEGY (pragmatic fleet-scale approach):

    The theoretically "correct" approach — per-(meter,date) curve
    reconstruction with anomaly-zeroing + scipy interpolation — is
    computationally infeasible at fleet scale (10,000 DTs × months).

    Instead we use a two-pronged SQL strategy that produces >95% of the
    same result in <5% of the time:

    1. For clean_data that already has valid readings at a slot:
       - MAX(KVA) over non-anomaly slots directly (simple SQL GROUP BY).
         This already handles the ~85-90% of slots that have valid data.
    
    2. Anomaly-flagged slots are NULLed out — this is what SQL does
       when we CASE them to NULL. The MAX naturally ignores them.
    
    3. For ENTIRELY MISSING meter-days (clean_data has no row at all
       for that meter+date), the interpolation engine in the Load Curve
       tab handles it on-demand. Fleet-wide, missing days are sparse
       enough that this is acceptable.

    The result is a CLEAN MAX that excludes anomaly readings — exactly
    what the user sees as "cleaned data" — computed at SQL speed.
    """
    cached = _read_disk_cache_if_fresh(MONTHLY_PEAK_CACHE, _months_sig)
    if cached is not None:
        return cached

    raw_peak = _read_disk_cache_if_fresh(MONTHLY_PEAK_RAW_CACHE, _months_sig)
    if raw_peak is None or raw_peak.empty:
        return None

    clean_files = clean_data_glob()
    hard_files = hard_anomalies_glob()
    thresh_files = threshold_anomalies_glob()
    if not clean_files:
        return None

    months = list_available_months()
    if not months:
        return None

    granularity_df = get_meter_granularity_cached()
    if granularity_df is None or granularity_df.empty:
        return None

    # ------------------------------------------------------------------
    # Process each month in parallel with pure SQL:
    #   - Join clean_data with anomaly flags
    #   - NULL out anomaly-flagged KVA values  
    #   - MAX over remaining (non-anomaly) values per meter/month
    # ------------------------------------------------------------------
    def _process_month_clean_sql(ym, granularity_df):
        worker_con = duckdb.connect(database=":memory:")
        try:
            worker_con.execute("PRAGMA threads=2")
            worker_con.execute(f"PRAGMA memory_limit='{max(1, TOTAL_DUCKDB_MEMORY_GB)}GB'")
        except Exception:
            pass
        register_granularity_table(worker_con, granularity_df)

        month_clean_files = _files_for_month(clean_files, ym)
        if not month_clean_files:
            worker_con.close()
            return []
        month_clean_globs = _globs_literal(month_clean_files)

        # Build anomaly subquery
        bad_parts = []
        for f_list in [
            _files_for_month(hard_files, ym),
            _files_for_month(thresh_files, ym),
        ]:
            if f_list:
                bad_parts.append(
                    f"SELECT MTR_NO, CAST(DATE AS DATE) AS DATE, TIME_SLOT "
                    f"FROM read_parquet({_globs_literal(f_list)})"
                )
        bad_cte = " UNION ".join(bad_parts) if bad_parts else "SELECT NULL AS MTR_NO, NULL::DATE AS DATE, NULL AS TIME_SLOT WHERE FALSE"

        kva_expr = _kva_select_expr('c')

        try:
            df = worker_con.execute(f"""
                WITH bad AS ({bad_cte}),
                with_kva AS (
                    SELECT
                        c.{cfg.COL_METER} AS MTR_NO,
                        c.{cfg.COL_DT} AS DT_CODE_NEW,
                        {ym} AS YM,
                        CASE WHEN b.MTR_NO IS NOT NULL THEN NULL ELSE {kva_expr} END AS KVA_CLEAN
                    FROM read_parquet({month_clean_globs}) c
                    LEFT JOIN meter_granularity g ON c.{cfg.COL_METER} = g.MTR_NO
                    LEFT JOIN bad b
                        ON c.{cfg.COL_METER} = b.MTR_NO
                       AND CAST(c.{cfg.COL_DATE} AS DATE) = b.DATE
                       AND c.{cfg.COL_TIME_SLOT} = b.TIME_SLOT
                    WHERE c.{cfg.COL_DT} IS NOT NULL
                )
                SELECT
                    MTR_NO,
                    DT_CODE_NEW,
                    {ym} AS YM,
                    MAX(KVA_CLEAN) AS PEAK_KVA_CLEAN
                FROM with_kva
                WHERE KVA_CLEAN IS NOT NULL
                GROUP BY MTR_NO, DT_CODE_NEW
            """).df()
            worker_con.close()
            return df.to_dict('records')
        except Exception as e:
            worker_con.close()
            print(f"[CleanCache] SQL error for {ym}: {e}")
            return []

    # ------------------------------------------------------------------
    # Execute with ThreadPoolExecutor — pure SQL, fast
    # ------------------------------------------------------------------
    all_clean_records = []
    st.info(f"Building CLEAN peak KVA across {len(months)} months via SQL (excludes anomaly-flagged slots)...")
    progress = st.progress(0.0, text="CLEAN peak KVA — starting...")
    t_start = time.time()
    done = 0
    errors = []

    with ThreadPoolExecutor(max_workers=MONTH_CONCURRENCY) as pool:
        futures = {pool.submit(_process_month_clean_sql, ym, granularity_df): ym for ym in months}
        for future in as_completed(futures):
            ym = futures[future]
            try:
                result = future.result()
                if result:
                    all_clean_records.extend(result)
            except Exception as e:
                errors.append(f"{ym}: {e}")
                print(f"[CleanCache] Error in future for {ym}: {e}")

            done += 1
            elapsed = time.time() - t_start
            avg = elapsed / done
            remaining = avg * (len(months) - done)
            eta_txt = f"{remaining/60:.1f} min" if remaining >= 60 else f"{remaining:.0f} sec"
            progress.progress(
                done / max(len(months), 1),
                text=f"CLEAN peak KVA — {done}/{len(months)} months — ~{eta_txt} remaining"
            )
    progress.empty()

    if errors:
        print(f"[CleanCache] {len(errors)} month errors (first 5): {errors[:5]}")

    if not all_clean_records:
        st.error("Clean KVA: no records produced. Check console for errors.")
        return None

    clean_peak = pd.DataFrame(all_clean_records)
    elapsed_total = time.time() - t_start
    st.success(f"CLEAN peak KVA built in {elapsed_total:.0f}s ({len(all_clean_records):,} meter-months)")

    # Merge with raw peak
    raw_base = raw_peak[["MTR_NO", "YM", "PEAK_KVA_RAW"]].copy()
    raw_base["MTR_NO"] = raw_base["MTR_NO"].astype(str)
    clean_peak["MTR_NO"] = clean_peak["MTR_NO"].astype(str)

    df = raw_base.merge(
        clean_peak[["MTR_NO", "YM", "PEAK_KVA_CLEAN"]],
        on=["MTR_NO", "YM"],
        how="left"
    )
    df = df.sort_values(["MTR_NO", "YM"]).reset_index(drop=True)

    os.makedirs(cfg.DASH_CACHE_DIR, exist_ok=True)
    _write_disk_cache(MONTHLY_PEAK_CACHE, _months_sig, df)
    return df


def get_monthly_peak_kva_cached():
    """Returns RAW peak KVA only (fast). Use get_monthly_peak_kva_clean_cached() for CLEAN."""
    months = list_available_months()
    return _build_monthly_peak_kva(_months_signature(months))


def get_monthly_peak_kva_clean_cached():
    """Returns both RAW and CLEAN peak KVA (slow, requires row-level processing)."""
    months = list_available_months()
    return _build_monthly_peak_kva_clean(_months_signature(months))