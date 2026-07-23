"""
DTS LOAD SURVEY PIPELINE -- CHUNKED, MEMORY-SAFE VERSION
===================================================================
Engine: DuckDB (disk-backed, out-of-core) + pandas for yearly master
workbooks + Parquet (columnar storage). Single machine, 16GB RAM.

===================================================================
PERF PASS (this version) -- what changed vs. the previous draft, and why
===================================================================
A colleague's (ChatGPT-generated) list of 17 optimizations was reviewed
against this actual pipeline. Some were already true, some didn't apply
to this design, and a few were real wins. Here's the honest breakdown:

APPLIED:
  - Dedup moved BEFORE the ASOF/meter_sequence joins (was already before
    CT scaling, but duplicate rows were still paying for two joins first).
    Now dedup happens right after the raw parquet read.
  - Stage 5 (hard-rule anomalies) rewrote from ~18 UNION ALL branches
    (18 full scans of base_month) into ONE pass that computes all flag
    booleans once, then expands them into long format via a single
    UNNEST of a struct list. This was the single biggest win on the list.
  - Stage 5 + Stage 6 merged into one function (stage56_...) so the flags
    computed once are reused for BOTH the hard_anomalies export AND the
    clean (nulled) dataset -- removes a second full scan of base_month.
  - Stage 4 (rolling threshold anomalies) had the same "N branches = N
    scans" problem (13 UNION ALL branches over the already-filtered
    with_thresholds table) -- also converted to a single UNNEST pass.
  - CT ratio (COALESCE(CT_RATIO,1)) is now computed once per row instead
    of being re-evaluated in every scaled column's expression.
  - THREADS is now capped at min(cpu_count(), 6) -- on a 16GB box, more
    threads mostly buys you more concurrent hash tables / more spilling,
    not more speed. This matches the memory-conscious design already used
    elsewhere in this file (MEMORY_LIMIT, temp_directory, etc.).
  - Export ROW_GROUP_SIZE bumped from 250,000 to 500,000 -- cheap, no
    real downside for the way this data gets scanned downstream
    (Streamlit/DuckDB/Polars full-column reads, not point lookups).
  - Collapsed the old 7-materialization join chain (raw_ext -> base_asof_ext
    -> base_ext -> base_ext_seq -> base_dedup -> scaled_ext -> base_month)
    down to 4 (raw_dedup -> base_joined -> scaled_ext -> base_month). The
    three join/coalesce steps that don't reduce row count were merged into
    one CREATE TABLE with internal CTEs, since DuckDB pipelines those in
    one pass anyway -- no reason to pay for three separate materializations.

DELIBERATELY NOT APPLIED (with reasons):
  - "Replace ALL temp tables with CTEs, never materialize" -- this file
    intentionally materializes at specific points (base_month above all)
    because downstream Stage 3/4/5/6 all re-read it multiple times, and
    this is a disk-backed, 16GB-RAM, out-of-core pipeline: forcing DuckDB
    to keep the whole chain unmaterialized risks pushing intermediate
    state into memory/spill in ways that are harder to reason about than
    "materialize the reused checkpoint, stream the rest." Applied the
    *spirit* of the suggestion (removed materializations that were pure
    pass-through and never re-read) without removing the load-bearing one.
  - "Avoid SELECT *" -- doesn't actually apply here. Stage 0 already
    projects the raw CSVs down to ~20 named columns before anything hits
    Parquet, so every downstream SELECT * is already only touching the
    narrow, needed column set. Rewriting every SELECT * to a 20-column
    explicit list throughout would add a lot of surface area for a
    column to be silently dropped, for no real I/O savings.
  - "Push DATE filter into read_parquet()" -- already effectively done:
    the code already selects exact SOURCE_YM partition folders (hive
    partition pruning) before ever filtering by date, and the date-range
    WHERE clause on OCCUR_D_RAW is DuckDB's standard filter-pushdown
    pattern for a scan predicate. There's no separate "unfiltered read
    then filter" step happening.
  - "ART index on master_timeaware(MTR_NO, MONTH)" -- skipped. ASOF JOIN
    in DuckDB uses its own sort/merge-style implementation, not a B-tree/
    ART index lookup, so building an index here mostly adds index-
    maintenance cost without speeding up the join it's meant to help.
    master_timeaware is also tiny, so there's very little to gain.
  - "One partitioned Parquet dataset instead of per-stage COPY" -- this
    pipeline already appends one new file per month per output type
    (never rewrites existing files), which is the actual goal of that
    suggestion. Restructuring into a single dataset partitioned by month
    wouldn't change the I/O pattern meaningfully here.
  - "Cache expected slots (48/96) instead of MODE()" -- expected slots
    are already computed from the small, pre-aggregated daily_slot_counts
    table, not from base_month, so it's cheap already. It's also
    genuinely per-meter (your smart-meter 15-min vs standard 30-min
    distinction means this SHOULD be allowed to vary), so hard-caching it
    risks being wrong across a meter replacement. Left as-is.

IMPORTANT: this pass changes HOW the anomaly/clean logic is computed,
not WHAT it computes -- every flag condition, anomaly-type string, and
severity was carried over unchanged so downstream dashboard code that
filters on e.g. 'PHASE_LOSS_KW_R' or 'EXTREME_VOLTAGE_VR' keeps working.
Still: bump LOGIC_VERSION (done below, v3->v4) and re-run 1-2 months to
diff row counts / anomaly counts against your existing v3 output before
trusting a full re-run across all months.

------------------------------------------------------------------
KW/KVAR NOW WIRED IN (confirmed present in raw CSVs: KW_R, KW_Y, KW_B,
KVAR_R, KVAR_Y, KVAR_B). Added to:
  - Stage 0 ingestion (explicit_types + SELECT)
  - CT-ratio scaling (same as current/energy -- these are also
    CT-dependent measured quantities)
  - Stage 4 rolling +-5% threshold check (param_cols)
  - Stage 5/6 hard-rule anomalies: MISSING_KWH, ZERO_KWH_WITH_LOAD
    (both re-added from the original 10-DT pilot, dropped somewhere
    along the way), and NEGATIVE_KVAR (KVAR below KVAR_NEG_THRESH)

NOT YET WIRED IN -- confirm meaning before adding:
  - KW_E, PF_B, PF_E: unclear whether these are "Export" vs "Bulk"
    readings, a 4th/neutral-adjacent phase, or something else --
    guessing wrong here would mislabel a real column. Tell me what
    these represent and I'll add them.
  - N_CUR (neutral current): very likely worth a hard-rule check --
    persistently high neutral current is a classic phase-imbalance /
    loose-neutral fault signature on a DT. Not added yet pending your
    confirmation this is neutral current (not something else) and
    what threshold makes sense (this one genuinely needs a DT-class-
    aware bound, unlike phase current, so a flat number is riskier).
  - FREQ: nominal 50Hz grid frequency -- could add a tight band (e.g.
    49-51Hz) as a hard-rule sensor-fault check. Skipped for now since
    you didn't ask for it; say the word if you want it in.
  - IVR/IVY/IVB: possible second voltage reading (redundant with
    VR/VY/VB?) or a different point of measurement -- needs
    clarification before use.

PIPELINE STAGES (per month, in a loop)
------------------------------------
  STAGE 0  Raw CSV -> partitioned Parquet          (one-time, cached, unchanged)
  STAGE 1  Load master mapping                      (one-time, unchanged)
  STAGE 2  Slice month + 7-day lookback, dedup, ASOF join, CT-ratio scaling
  STAGE 3  Missing slot / missing date detection (month-only rows)
  STAGE 4  7-day rolling threshold anomalies (+-5%)  (uses lookback rows for baseline)
  STAGE 5+6 Hard-rule anomalies + clean dataset, computed in one merged pass
  STAGE 7  Export month's outputs (Parquet, appended per-month subfolder)

Install once:
  py -3.12 -m pip install duckdb pandas pyxlsb openpyxl

Usage:
  py -3.12 dt_pipeline_chunked.py
"""

import duckdb
import os
import re
import time
import datetime

# ---------------------------------------------------------------------------
# CONFIGURE
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CACHE-INVALIDATION VERSIONS -- bump these when you change the pipeline,
# NOT the folders themselves. This is what lets you edit anomaly logic
# without re-reading 300GB of CSV every time, while still guaranteeing
# stale output never silently lingers next to fresh output.
#
#   RAW_SCHEMA_VERSION : bump ONLY when Stage 0's column SELECTION changes
#                        (adding/removing/renaming a raw column). Bumping
#                        this forces a full CSV->Parquet re-ingestion --
#                        expensive, so only do it when you actually must.
#   LOGIC_VERSION       : bump when anomaly/cleaning/threshold LOGIC changes
#                        (new hard-rule check, changed bounds, MHVD exemption
#                        tweak, etc.) but the raw columns themselves didn't
#                        change. Bumping this reprocesses every month from
#                        the cached raw Parquet -- much cheaper than a full
#                        re-ingestion, since it skips Stage 0 entirely.
#
# Bumped today: v3 -> v4. This is a PERFORMANCE refactor of Stage 2 (dedup
# ordering), Stage 4, and Stage 5/6 (single-pass instead of many UNION ALL
# scans). Every flag condition and output column was carried over
# unchanged, but bumping the version forces a re-run so you can diff
# output against the old v3 files before trusting it fully.
# ---------------------------------------------------------------------------
RAW_SCHEMA_VERSION = "2"     # v1: VR/VY/VB, IR/IY/IB, KWH_TOTAL only. v2: + KW_R/Y/B, KVAR_R/Y/B
LOGIC_VERSION = "4"          # v1: base hard/threshold rules. v2: + MISSING_KWH, ZERO_KWH_WITH_LOAD,
                             # NEGATIVE_KVAR. v3: + row dedup on (MTR_NO,DATE,TIME_SLOT), meter-
                             # replacement chain tagging + DT-level stitched coverage, per-phase
                             # KW/KVAR "single phase loss" hard anomaly. v4: performance refactor --
                             # dedup moved before joins, Stage 4 and Stage 5/6 rewritten as single-
                             # pass (struct-list UNNEST) instead of many UNION ALL scans. Same outputs.

RAW_MONTHLY_FOLDER = r"C:\Users\Siddharth Jain\Documents\BRPL DTS\Raw Files monthly"
MASTER_FOLDER = r"C:\Users\Siddharth Jain\Documents\BRPL DTS\csv 2021-2026"
OUTPUT_DIR = r"C:\Users\Siddharth Jain\Documents\BRPL DTS\pipeline_output"
TEMP_DIR = r"C:\Users\Siddharth Jain\Documents\BRPL DTS\duckdb_tmp"        # point at disk with most free space
DB_PATH = os.path.join(OUTPUT_DIR, "pipeline.duckdb")                     # disk-backed, not in-memory
STATUS_DIR = os.path.join(OUTPUT_DIR, "month_status") 
                    # per-month "done" markers

RAW_METER_COL = "MTR_NO"
RAW_DATE_COL = "OCCUR_D"
RAW_TIME_COL = "OCCUR_T"
VOLT_COLS = ["VR", "VY", "VB"]
CURR_COLS = ["IR", "IY", "IB"]
ENERGY_COL = "KWH_ABS"
KW_COLS = ["KW_R", "KW_Y", "KW_B"]        # confirmed present in raw CSV
KVAR_COLS = ["KVAR_R", "KVAR_Y", "KVAR_B"]  # confirmed present in raw CSV

MASTER_METER_COL = "DT_METER_NO"
MASTER_DT_COL = "DT_CODE_NEW"
MASTER_FL_COL = "SDO_CD"
MASTER_CT_COL = "CT_RATIO"
MASTER_MONTH_COL = "MONTH"
MASTER_KVA_COL = "KVA_RATING"
MASTER_STATUS_COL = "DT_STS"
MASTER_CONSUMER_COUNT_COL = "CONSUMER_COUNT"
MASTER_DT_CAT_COL = "DT_STS"          # DT category lives in DT_STS, not DT_CATEGORY (that holds area/loss labels)

# Categories exempt from the flat V/I hard bounds (legitimately run higher V/I).
EXEMPT_DT_CATEGORIES = ("MHVD", "HVDAF")

CT_SCALING_ENABLED = True

V_MIN, V_MAX = 180.0, 290.0
I_SPIKE_MAX = 5000.0
KVAR_NEG_THRESH = -10.0

# Individual-phase KW/KVAR "one phase dead, others loaded" check.
# ZERO_VOLTAGE requires ALL 3 phases at 0 (total outage); this catches the
# opposite pattern -- ONE phase near-zero while the other two carry real
# load, which is a classic single-phase-loss / blown-fuse / loose-
# connection fault signature that a whole-DT check would never see.
# Threshold is a small absolute floor (kW/kVAr, post CT-scaling) below
# which a phase is treated as "not carrying load" -- tune if too
# sensitive/insensitive for your DT size range.
PHASE_LOSS_EPS_KW = 0.02
PHASE_LOSS_EPS_KVAR = 0.02

ROLLING_WINDOW_DAYS = 7
PCT_BAND = 0.05

# PERF: capped at 6 -- on a 16GB box, letting DuckDB use every core just
# means more concurrent hash tables / more spilling, not more speed.
THREADS = min(os.cpu_count() or 4, 6)
MEMORY_LIMIT = "12GB"   # bumped from 10GB -- Stage 1's pandas/Excel load is now cached (skipped on
                        # repeat runs), so there's less peak memory pressure to leave headroom for
MAX_TEMP_DIR_SIZE = "40GiB"
PARQUET_COMPRESSION = "SNAPPY"
EXPORT_ROW_GROUP_SIZE = 500_000   # PERF: was 250,000 -- larger row groups suit the full-column-scan
                                   # access pattern used by Streamlit/DuckDB/Polars/PyArrow downstream


def get_con():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"PRAGMA threads={THREADS}")
    con.execute(f"PRAGMA memory_limit='{MEMORY_LIMIT}'")
    con.execute(f"PRAGMA temp_directory='{TEMP_DIR.replace(chr(92), '/')}'")
    con.execute(f"PRAGMA max_temp_directory_size='{MAX_TEMP_DIR_SIZE}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("PRAGMA enable_progress_bar")
    return con


# ═══════════════════════════════════════════════════════════════════════
# STAGE 0: Raw CSV -> partitioned Parquet (one-time, then always reused)
# ═══════════════════════════════════════════════════════════════════════
def stage0_csv_to_parquet(con):
    parquet_path = os.path.join(OUTPUT_DIR, "raw_parquet")
    marker = os.path.join(parquet_path, "_SUCCESS")

    if os.path.exists(marker):
        with open(marker) as f:
            cached_version = f.read().strip()
        if cached_version == RAW_SCHEMA_VERSION:
            print(f"STAGE 0: Parquet already exists at schema v{RAW_SCHEMA_VERSION}, skipping re-ingestion.")
            return parquet_path
        else:
            print(f"STAGE 0: Cached Parquet is schema v{cached_version}, code now expects v{RAW_SCHEMA_VERSION} "
                  f"(raw column selection changed) -- rebuilding raw_parquet from CSV. This is the expensive "
                  f"step; it will NOT run again until RAW_SCHEMA_VERSION changes.")
            import shutil
            shutil.rmtree(parquet_path)

    print("STAGE 0: Converting raw CSVs to partitioned Parquet (one-time)...")
    os.makedirs(parquet_path, exist_ok=True)
    pattern = os.path.join(RAW_MONTHLY_FOLDER, "*.csv")

    full_threads = os.cpu_count() or 4
    con.execute(f"PRAGMA threads={full_threads}")

    explicit_types = {
        RAW_METER_COL: "VARCHAR",
        **{c: "DOUBLE" for c in VOLT_COLS},
        **{c: "DOUBLE" for c in CURR_COLS},
        **{c: "DOUBLE" for c in KW_COLS},
        **{c: "DOUBLE" for c in KVAR_COLS},
        ENERGY_COL: "DOUBLE",
    }
    types_literal = "{" + ", ".join(f"'{k}': '{v}'" for k, v in explicit_types.items()) + "}"

    start = time.time()
    con.execute(f"""
        COPY (
            SELECT
                CAST("{RAW_METER_COL}" AS VARCHAR) AS MTR_NO,
                "{RAW_DATE_COL}"   AS OCCUR_D_RAW,
                "{RAW_TIME_COL}"   AS TIME_SLOT,
                {", ".join(f'"{c}"' for c in VOLT_COLS)},
                {", ".join(f'"{c}"' for c in CURR_COLS)},
                {", ".join(f'"{c}"' for c in KW_COLS)},
                {", ".join(f'"{c}"' for c in KVAR_COLS)},
                "{ENERGY_COL}"     AS KWH_TOTAL,
                CAST(substr(filename, -10, 6) AS INTEGER) AS SOURCE_YM
            FROM read_csv_auto('{pattern}', union_by_name = true, ignore_errors = true, filename = true,
                                types = {types_literal})
        ) TO '{parquet_path}'
        (FORMAT PARQUET, PARTITION_BY (SOURCE_YM), OVERWRITE_OR_IGNORE 1, COMPRESSION 'SNAPPY')
    """)
    con.execute(f"PRAGMA threads={THREADS}")
    with open(marker, "w") as f:
        f.write(RAW_SCHEMA_VERSION)
    print(f"  Done in {time.time() - start:.1f}s")
    return parquet_path


# ═══════════════════════════════════════════════════════════════════════
# STAGE 1: Load master mapping (one-time, small)
# ═══════════════════════════════════════════════════════════════════════
def stage1_load_master(con):
    master_cache = os.path.join(OUTPUT_DIR, "master_cache.parquet")
    earliest_cache = os.path.join(OUTPUT_DIR, "earliest_master_cache.parquet")
    meter_seq_cache = os.path.join(OUTPUT_DIR, "meter_seq_cache.parquet")

    if os.path.exists(master_cache) and os.path.exists(earliest_cache) and os.path.exists(meter_seq_cache):
        print("STAGE 1: Master cache found -- loading from Parquet, skipping Excel/pandas entirely.")
        con.execute(f"CREATE OR REPLACE TABLE master_timeaware AS SELECT * FROM read_parquet('{master_cache}')")
        con.execute(f"CREATE OR REPLACE TABLE earliest_master AS SELECT * FROM read_parquet('{earliest_cache}')")
        con.execute(f"CREATE OR REPLACE TABLE meter_sequence AS SELECT * FROM read_parquet('{meter_seq_cache}')")
        n = con.execute("SELECT COUNT(*) FROM master_timeaware").fetchone()[0]
        print(f"  Master mapping loaded from cache: {n} (meter, month) rows")
        return

    print("STAGE 1: No cache found -- loading TIME-AWARE master DT/meter/CT-ratio/DT_CAT mapping from Excel...")
    import pandas as pd

    year_pattern = re.compile(r"(20\d{2})")
    all_rows = []
    for fname in os.listdir(MASTER_FOLDER):
        if fname.startswith("~$"):
            continue
        if not fname.lower().endswith((".xlsb", ".xlsx", ".csv")):
            continue
        if not year_pattern.search(fname):
            continue
        fpath = os.path.join(MASTER_FOLDER, fname)
        ext = os.path.splitext(fpath)[1].lower()
        try:
            if ext == ".xlsb":
                sheets = pd.read_excel(fpath, sheet_name=None, engine="calamine")
            elif ext == ".xlsx":
                sheets = pd.read_excel(fpath, sheet_name=None, engine="calamine")
            else:
                sheets = {"sheet": pd.read_csv(fpath)}
        except Exception:
            if ext == ".xlsb":
                sheets = pd.read_excel(fpath, sheet_name=None, engine="pyxlsb")
            elif ext == ".xlsx":
                sheets = pd.read_excel(fpath, sheet_name=None, engine="openpyxl")
            else:
                raise
        for sheet_name, sdf in sheets.items():
            sdf = sdf.copy()
            sdf.columns = [str(c).strip() for c in sdf.columns]
            required = [MASTER_METER_COL, MASTER_DT_COL, MASTER_FL_COL, MASTER_CT_COL, MASTER_MONTH_COL]
            optional = [MASTER_KVA_COL, MASTER_STATUS_COL, MASTER_DT_CAT_COL, MASTER_CONSUMER_COUNT_COL]
            keep = list(dict.fromkeys([c for c in required + optional if c in sdf.columns]))
            if MASTER_MONTH_COL not in keep:
                print(f"  WARNING: '{fname}' sheet '{sheet_name}' has no MONTH column -- skipping this sheet.")
                continue
            if MASTER_METER_COL in keep and MASTER_DT_COL in keep:
                all_rows.append(sdf[keep])

    master_df = pd.concat(all_rows, ignore_index=True).drop_duplicates()
    master_df[MASTER_METER_COL] = master_df[MASTER_METER_COL].astype(str).str.strip()
    master_df[MASTER_MONTH_COL] = pd.to_numeric(master_df[MASTER_MONTH_COL], errors="coerce").astype("Int64")

    con.register("master_raw", master_df)

    optional_select = ""
    if MASTER_KVA_COL in master_df.columns:
        optional_select += f', TRY_CAST("{MASTER_KVA_COL}" AS DOUBLE) AS KVA_RATING'
    if MASTER_STATUS_COL in master_df.columns:
        optional_select += f', "{MASTER_STATUS_COL}" AS DT_STS'
    if MASTER_DT_CAT_COL in master_df.columns:
        optional_select += f', "{MASTER_DT_CAT_COL}" AS DT_CAT'
    if MASTER_CONSUMER_COUNT_COL in master_df.columns:
        optional_select += f', TRY_CAST("{MASTER_CONSUMER_COUNT_COL}" AS DOUBLE) AS CONSUMER_COUNT'

    con.execute(f"""
        CREATE OR REPLACE TABLE master_timeaware AS
        SELECT DISTINCT
            "{MASTER_METER_COL}" AS MTR_NO,
            "{MASTER_MONTH_COL}" AS MONTH,
            "{MASTER_DT_COL}"    AS DT_CODE_NEW,
            "{MASTER_FL_COL}"    AS SDO_CD,
            TRY_CAST("{MASTER_CT_COL}" AS DOUBLE) AS CT_RATIO
            {optional_select}
        FROM master_raw
        WHERE "{MASTER_METER_COL}" IS NOT NULL AND "{MASTER_MONTH_COL}" IS NOT NULL
        ORDER BY MTR_NO, MONTH
    """)
    n = con.execute("SELECT COUNT(*) FROM master_timeaware").fetchone()[0]
    print(f"  Master mapping loaded: {n} (meter, month) rows")

    con.execute("""
        CREATE OR REPLACE TABLE earliest_master AS
        SELECT MTR_NO, DT_CODE_NEW, SDO_CD, CT_RATIO, DT_CAT
        FROM master_timeaware
        QUALIFY ROW_NUMBER() OVER (PARTITION BY MTR_NO ORDER BY MONTH ASC) = 1
    """)

    # Explicit meter-replacement chain tagging. For each DT_CODE_NEW, rank
    # the distinct meters that have ever reported under it by the MONTH
    # they first appear. meter_seq=1 is the original meter; meter_seq=2+
    # means the DT had its meter physically replaced. is_meter_replaced is
    # a per-DT flag (true if that DT EVER had >1 meter across its whole
    # history) -- this is what lets a dashboard show "this DT's meter was
    # swapped" without digging through raw history.
    con.execute("""
        CREATE OR REPLACE TABLE meter_sequence AS
        WITH first_month_per_meter AS (
            SELECT DT_CODE_NEW, MTR_NO, MIN(MONTH) AS first_month
            FROM master_timeaware
            GROUP BY DT_CODE_NEW, MTR_NO
        ),
        ranked AS (
            SELECT DT_CODE_NEW, MTR_NO, first_month,
                   ROW_NUMBER() OVER (PARTITION BY DT_CODE_NEW ORDER BY first_month ASC) AS meter_seq
            FROM first_month_per_meter
        ),
        dt_replaced AS (
            SELECT DT_CODE_NEW, (MAX(meter_seq) > 1) AS is_meter_replaced
            FROM ranked
            GROUP BY DT_CODE_NEW
        )
        SELECT r.DT_CODE_NEW, r.MTR_NO, r.meter_seq,
               'meter_' || r.meter_seq AS dt_meter_rank,
               d.is_meter_replaced
        FROM ranked r
        JOIN dt_replaced d USING (DT_CODE_NEW)
    """)
    n_replaced = con.execute(
        "SELECT COUNT(DISTINCT DT_CODE_NEW) FROM meter_sequence WHERE is_meter_replaced"
    ).fetchone()[0]
    n_dts_total = con.execute("SELECT COUNT(DISTINCT DT_CODE_NEW) FROM meter_sequence").fetchone()[0]
    print(f"  Meter-replacement chains: {n_replaced} / {n_dts_total} DTs have had >1 physical meter")

    cat_dist = con.execute("""
        SELECT DT_CAT, COUNT(DISTINCT MTR_NO) AS n_meters
        FROM master_timeaware GROUP BY DT_CAT ORDER BY n_meters DESC
    """).fetchall()
    print(f"  DT_CAT (from {MASTER_DT_CAT_COL}) distribution: {cat_dist}")

    con.execute(f"COPY master_timeaware TO '{master_cache}' (FORMAT PARQUET)")
    con.execute(f"COPY earliest_master TO '{earliest_cache}' (FORMAT PARQUET)")
    con.execute(f"COPY meter_sequence TO '{meter_seq_cache}' (FORMAT PARQUET)")
    print(f"  Master mapping cached to Parquet -- future runs will skip Excel/pandas entirely.")
    print(f"  Delete {master_cache}, {earliest_cache}, and {meter_seq_cache} if the master "
          f"workbooks change and need reloading.")


# ═══════════════════════════════════════════════════════════════════════
# Helpers for month chunking
# ═══════════════════════════════════════════════════════════════════════
def prev_ym(ym):
    y, m = divmod(ym, 100)
    if m == 1:
        return (y - 1) * 100 + 12
    return y * 100 + (m - 1)


def month_bounds(ym):
    y, m = divmod(ym, 100)
    start = datetime.date(y, m, 1)
    if m == 12:
        end = datetime.date(y + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        end = datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)
    return start, end


# ═══════════════════════════════════════════════════════════════════════
# Per-month: dedup, ASOF join, CT scaling
#
# PERF (v4): order changed from
#     raw_ext -> ASOF join -> coalesce -> meter_seq join -> DEDUP -> CT scale
# to
#     raw + DEDUP -> ASOF join -> coalesce -> meter_seq join (merged into
#     one materialization) -> CT scale
# Duplicate rows (retransmissions / month-boundary overlaps) no longer pay
# for an ASOF join and a meter_sequence join before being thrown away. The
# three join/coalesce steps that don't change row count are now built as
# CTEs inside a single CREATE TABLE, since DuckDB pipelines them in one
# pass anyway -- no benefit to materializing each individually.
# ═══════════════════════════════════════════════════════════════════════
def build_month_extended(con, ym, parquet_path):
    month_start, month_end = month_bounds(ym)
    lookback_start = month_start - datetime.timedelta(days=ROLLING_WINDOW_DAYS)
    p_ym = prev_ym(ym)

    partition_globs = [f"{parquet_path}/SOURCE_YM={ym}/*.parquet"]
    if os.path.isdir(os.path.join(parquet_path, f"SOURCE_YM={p_ym}")):
        partition_globs.append(f"{parquet_path}/SOURCE_YM={p_ym}/*.parquet")
    globs_literal = "[" + ", ".join(f"'{g}'" for g in partition_globs) + "]"

    # Raw read + row-level dedup on (MTR_NO, DATE, TIME_SLOT), done BEFORE
    # any join. A duplicate reading would otherwise silently double-count
    # in every downstream aggregate -- energy totals, health %, coverage,
    # all of it. Tie-break: keep the row with the FEWEST nulls among the
    # key electrical columns (prefer the more complete reading over a
    # partially-null duplicate); arbitrary if still tied.
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE raw_dedup AS
        SELECT * EXCLUDE (_dup_rank) FROM (
            SELECT *,
                CAST(OCCUR_D_RAW AS DATE) AS DATE,
                CAST(strftime(CAST(OCCUR_D_RAW AS DATE), '%Y%m') AS INTEGER) AS DATA_MONTH,
                ROW_NUMBER() OVER (
                    PARTITION BY MTR_NO, CAST(OCCUR_D_RAW AS DATE), TIME_SLOT
                    ORDER BY
                        (CASE WHEN VR IS NULL THEN 1 ELSE 0 END
                       + CASE WHEN IR IS NULL THEN 1 ELSE 0 END
                       + CASE WHEN KWH_TOTAL IS NULL THEN 1 ELSE 0 END) ASC
                ) AS _dup_rank
            FROM read_parquet({globs_literal}, hive_partitioning=1)
            WHERE CAST(OCCUR_D_RAW AS DATE) >= DATE '{lookback_start}'
              AND CAST(OCCUR_D_RAW AS DATE) <= DATE '{month_end}'
        )
        WHERE _dup_rank = 1
    """)

    # ASOF join to master -> coalesce nulls with earliest_master -> attach
    # meter-replacement-chain context. Merged into one materialization
    # (was three separate TEMP TABLEs) since none of these steps change
    # row count -- DuckDB fuses them into a single pipeline internally.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE base_joined AS
        WITH asof_joined AS (
            SELECT r.*, m.DT_CODE_NEW, m.SDO_CD, m.CT_RATIO, m.DT_CAT
            FROM raw_dedup r
            ASOF LEFT JOIN master_timeaware m
                ON r.MTR_NO = m.MTR_NO AND r.DATA_MONTH >= m.MONTH
        ),
        coalesced AS (
            SELECT
                a.* EXCLUDE (DT_CODE_NEW, SDO_CD, CT_RATIO, DT_CAT),
                COALESCE(a.DT_CODE_NEW, e.DT_CODE_NEW) AS DT_CODE_NEW,
                COALESCE(a.SDO_CD, e.SDO_CD) AS SDO_CD,
                COALESCE(a.CT_RATIO, e.CT_RATIO) AS CT_RATIO,
                COALESCE(a.DT_CAT, e.DT_CAT) AS DT_CAT
            FROM asof_joined a
            LEFT JOIN earliest_master e ON a.MTR_NO = e.MTR_NO
        )
        SELECT c.*, ms.meter_seq, ms.dt_meter_rank,
               COALESCE(ms.is_meter_replaced, FALSE) AS is_meter_replaced
        FROM coalesced c
        LEFT JOIN meter_sequence ms
            ON c.MTR_NO = ms.MTR_NO AND c.DT_CODE_NEW = ms.DT_CODE_NEW
    """)
    con.execute("DROP TABLE IF EXISTS raw_dedup")

    if CT_SCALING_ENABLED:
        # CT ratio computed ONCE per row (was COALESCE(CT_RATIO,1) repeated
        # in every scaled column's expression) and reused across all the
        # CT-dependent measured quantities: current, KW, KVAR, energy.
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE scaled_ext AS
            SELECT
                * EXCLUDE ({", ".join(CURR_COLS + KW_COLS + KVAR_COLS)}, KWH_TOTAL, ct),
                {", ".join(f'TRY_CAST("{c}" AS DOUBLE) * ct AS "{c}"' for c in CURR_COLS)},
                {", ".join(f'TRY_CAST("{c}" AS DOUBLE) * ct AS "{c}"' for c in KW_COLS)},
                {", ".join(f'TRY_CAST("{c}" AS DOUBLE) * ct AS "{c}"' for c in KVAR_COLS)},
                TRY_CAST(KWH_TOTAL AS DOUBLE) * ct AS KWH_TOTAL,
                (CT_RATIO IS NOT NULL) AS ct_scaled
            FROM (SELECT *, COALESCE(CT_RATIO, 1) AS ct FROM base_joined)
        """)
    else:
        con.execute("CREATE OR REPLACE TEMP TABLE scaled_ext AS SELECT *, FALSE AS ct_scaled FROM base_joined")
    con.execute("DROP TABLE IF EXISTS base_joined")

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE base_month AS
        SELECT * FROM scaled_ext
        WHERE DATE >= DATE '{month_start}'
          AND DATE <= DATE '{month_end}'
    """)
    return month_start, month_end


# ═══════════════════════════════════════════════════════════════════════
# STAGE 3: Missing slots (month-only rows)
# ═══════════════════════════════════════════════════════════════════════
def stage3_missing_slots(con):
    con.execute("""
        CREATE OR REPLACE TEMP TABLE daily_slot_counts AS
        SELECT MTR_NO, DT_CODE_NEW, DATE, COUNT(*) AS slots_present
        FROM base_month
        GROUP BY MTR_NO, DT_CODE_NEW, DATE
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE meter_expected_slots AS
        SELECT MTR_NO, MODE(slots_present) AS expected_slots_per_day
        FROM daily_slot_counts WHERE slots_present > 0
        GROUP BY MTR_NO
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE missing_slot_days AS
        SELECT d.*, e.expected_slots_per_day,
            e.expected_slots_per_day - d.slots_present AS missing_slot_count,
            CASE WHEN d.slots_present = 0 THEN 'FULL_DAY_OFFLINE'
                 ELSE 'PARTIAL_' || d.slots_present || '_OF_' || e.expected_slots_per_day END AS GAP_TYPE
        FROM daily_slot_counts d
        JOIN meter_expected_slots e ON d.MTR_NO = e.MTR_NO
        WHERE d.slots_present < e.expected_slots_per_day
    """)

    # DT-LEVEL stitching. daily_slot_counts/missing_slot_days above are
    # keyed by (MTR_NO, DATE) -- a DT that replaced its meter mid-history
    # would show as two separate, fragmented series there. base_month_dt
    # dedupes to (DT_CODE_NEW, DATE, TIME_SLOT), preferring the row from the
    # highest meter_seq (the currently-active meter) whenever old and new
    # meters both reported on a changeover day, giving one continuous
    # per-DT timeline instead of per-meter fragments. This is what a
    # dashboard's "coverage for DT X" view should actually query.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE base_month_dt AS
        SELECT * EXCLUDE (_dt_rank) FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY DT_CODE_NEW, DATE, TIME_SLOT
                    ORDER BY COALESCE(meter_seq, 1) DESC
                ) AS _dt_rank
            FROM base_month
            WHERE DT_CODE_NEW IS NOT NULL
        )
        WHERE _dt_rank = 1
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE daily_slot_counts_dt AS
        SELECT DT_CODE_NEW, DATE, COUNT(*) AS slots_present
        FROM base_month_dt
        GROUP BY DT_CODE_NEW, DATE
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE dt_expected_slots AS
        SELECT DT_CODE_NEW, MODE(slots_present) AS expected_slots_per_day
        FROM daily_slot_counts_dt WHERE slots_present > 0
        GROUP BY DT_CODE_NEW
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE missing_slot_days_dt AS
        SELECT d.*, e.expected_slots_per_day,
            e.expected_slots_per_day - d.slots_present AS missing_slot_count,
            CASE WHEN d.slots_present = 0 THEN 'FULL_DAY_OFFLINE'
                 ELSE 'PARTIAL_' || d.slots_present || '_OF_' || e.expected_slots_per_day END AS GAP_TYPE
        FROM daily_slot_counts_dt d
        JOIN dt_expected_slots e ON d.DT_CODE_NEW = e.DT_CODE_NEW
        WHERE d.slots_present < e.expected_slots_per_day
    """)


# ═══════════════════════════════════════════════════════════════════════
# STAGE 4: 7-day rolling threshold anomalies -- includes KW/KVAR
#
# PERF (v4): the old code computed HI/LO once (good, single window scan)
# but then ran ONE UNION ALL branch PER param_col (13 of them) to work out
# which specific column(s) triggered. Rewritten to compute a struct per
# column in one pass and UNNEST the (already-filtered-to-anomalous) list --
# same output, one scan of with_thresholds instead of 13.
# ═══════════════════════════════════════════════════════════════════════
def stage4_rolling_threshold_anomalies(con, month_start, month_end):
    param_cols = VOLT_COLS + CURR_COLS + KW_COLS + KVAR_COLS + ["KWH_TOTAL"]

    hi_lo_cols = ", ".join(
        f'MAX("{c}") OVER w AS "{c}_HI", MIN("{c}") OVER w AS "{c}_LO"' for c in param_cols
    )
    any_anomaly_expr = " OR ".join(
        f'''("{c}_HI" IS NOT NULL AND "{c}_LO" IS NOT NULL
            AND ("{c}" > "{c}_HI" * (1 + {PCT_BAND})
                 OR ("{c}_LO" > 0 AND "{c}" < "{c}_LO" * (1 - {PCT_BAND}))))'''
        for c in param_cols
    )
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE with_thresholds AS
        SELECT MTR_NO, DT_CODE_NEW, DATE, TIME_SLOT,
               {", ".join(f'"{c}"' for c in param_cols)},
               {hi_lo_cols}
        FROM scaled_ext
        WINDOW w AS (
            PARTITION BY MTR_NO, TIME_SLOT ORDER BY DATE
            ROWS BETWEEN {ROLLING_WINDOW_DAYS} PRECEDING AND 1 PRECEDING
        )
        QUALIFY DATE >= DATE '{month_start}' AND DATE <= DATE '{month_end}'
               AND ({any_anomaly_expr})
    """)

    flag_exprs = []
    for c in param_cols:
        flag_exprs.append(f"""
            CASE
                WHEN "{c}_HI" IS NOT NULL AND "{c}_LO" IS NOT NULL
                     AND "{c}" > "{c}_HI" * (1 + {PCT_BAND})
                THEN {{'PARAMETER': '{c}', 'VALUE': "{c}", 'THRESH_HI': "{c}_HI", 'THRESH_LO': "{c}_LO", 'DIRECTION': 'HIGH'}}
                WHEN "{c}_HI" IS NOT NULL AND "{c}_LO" IS NOT NULL
                     AND "{c}_LO" > 0 AND "{c}" < "{c}_LO" * (1 - {PCT_BAND})
                THEN {{'PARAMETER': '{c}', 'VALUE': "{c}", 'THRESH_HI': "{c}_HI", 'THRESH_LO': "{c}_LO", 'DIRECTION': 'LOW'}}
            END
        """)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE threshold_anomalies AS
        SELECT MTR_NO, DT_CODE_NEW, DATE, TIME_SLOT,
               rec.PARAMETER, rec.VALUE, rec.THRESH_HI, rec.THRESH_LO, rec.DIRECTION
        FROM with_thresholds,
        UNNEST(list_filter([{",".join(flag_exprs)}], x -> x IS NOT NULL)) AS u(rec)
    """)
    n = con.execute("SELECT COUNT(*) FROM threshold_anomalies").fetchone()[0]
    print(f"  Threshold anomalies flagged: {n}")


# ═══════════════════════════════════════════════════════════════════════
# STAGE 5+6 (merged): Hard-rule anomalies + clean dataset
#
# PERF (v4): the old code ran Stage 5 as ~18 UNION ALL branches over
# base_month (18 full scans) to produce hard_anomalies, then Stage 6
# re-scanned base_month a 19th time with the same conditions repeated
# inline to build clean. Rewritten so every flag is computed exactly once
# in row_flags (one scan of base_month), then:
#   - hard_anomalies is built by UNNEST-ing a struct list of the flags
#     that are true (one scan of row_flags, not base_month)
#   - clean is built directly from row_flags's precomputed booleans
#     (another scan of row_flags, still not base_month)
# Every ANOMALY_TYPE string, SEVERITY, and clean-column rule is identical
# to the original -- this only changes how many times the data gets read.
# ═══════════════════════════════════════════════════════════════════════
def stage56_hard_anomalies_and_clean(con):
    exempt_list = ", ".join(f"'{c}'" for c in EXEMPT_DT_CATEGORIES)
    has_current_expr = " OR ".join(f'"{c}" > 0' for c in CURR_COLS)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE row_flags AS
        SELECT
            MTR_NO, DT_CODE_NEW, DT_CAT, DATE, TIME_SLOT,
            VR, VY, VB, IR, IY, IB, KW_R, KW_Y, KW_B, KVAR_R, KVAR_Y, KVAR_B, KWH_TOTAL,
            ct_scaled, meter_seq, dt_meter_rank, is_meter_replaced,

            (VR = 0 AND VY = 0 AND VB = 0) AS f_zero_v,
            (ne AND VR > 0 AND (VR < {V_MIN} OR VR > {V_MAX})) AS f_ext_v_vr,
            (ne AND VY > 0 AND (VY < {V_MIN} OR VY > {V_MAX})) AS f_ext_v_vy,
            (ne AND VB > 0 AND (VB < {V_MIN} OR VB > {V_MAX})) AS f_ext_v_vb,

            (ne AND IR > {I_SPIKE_MAX}) AS f_ext_i_ir,
            (ne AND IY > {I_SPIKE_MAX}) AS f_ext_i_iy,
            (ne AND IB > {I_SPIKE_MAX}) AS f_ext_i_ib,

            (KWH_TOTAL IS NULL AND VR IS NOT NULL AND VR > 0) AS f_missing_kwh,
            (KWH_TOTAL = 0 AND ({has_current_expr})) AS f_zero_kwh_load,

            (KVAR_R < {KVAR_NEG_THRESH}) AS f_neg_kvar_r,
            (KVAR_Y < {KVAR_NEG_THRESH}) AS f_neg_kvar_y,
            (KVAR_B < {KVAR_NEG_THRESH}) AS f_neg_kvar_b,

            (ABS(KW_R) <= {PHASE_LOSS_EPS_KW} AND (ABS(KW_Y) > {PHASE_LOSS_EPS_KW} OR ABS(KW_B) > {PHASE_LOSS_EPS_KW})) AS f_ploss_kw_r,
            (ABS(KW_Y) <= {PHASE_LOSS_EPS_KW} AND (ABS(KW_R) > {PHASE_LOSS_EPS_KW} OR ABS(KW_B) > {PHASE_LOSS_EPS_KW})) AS f_ploss_kw_y,
            (ABS(KW_B) <= {PHASE_LOSS_EPS_KW} AND (ABS(KW_R) > {PHASE_LOSS_EPS_KW} OR ABS(KW_Y) > {PHASE_LOSS_EPS_KW})) AS f_ploss_kw_b,

            (ABS(KVAR_R) <= {PHASE_LOSS_EPS_KVAR} AND (ABS(KVAR_Y) > {PHASE_LOSS_EPS_KVAR} OR ABS(KVAR_B) > {PHASE_LOSS_EPS_KVAR})) AS f_ploss_kvar_r,
            (ABS(KVAR_Y) <= {PHASE_LOSS_EPS_KVAR} AND (ABS(KVAR_R) > {PHASE_LOSS_EPS_KVAR} OR ABS(KVAR_B) > {PHASE_LOSS_EPS_KVAR})) AS f_ploss_kvar_y,
            (ABS(KVAR_B) <= {PHASE_LOSS_EPS_KVAR} AND (ABS(KVAR_R) > {PHASE_LOSS_EPS_KVAR} OR ABS(KVAR_Y) > {PHASE_LOSS_EPS_KVAR})) AS f_ploss_kvar_b
        FROM (
            SELECT *, (DT_CAT IS NULL OR DT_CAT NOT IN ({exempt_list})) AS ne
            FROM base_month
        )
    """)

    # hard_anomalies: long format, one row per (record, triggered flag) --
    # identical shape/strings to the original UNION ALL version.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE hard_anomalies AS
        SELECT MTR_NO, DT_CODE_NEW, DT_CAT, DATE, TIME_SLOT, rec.ANOMALY_TYPE, rec.SEVERITY
        FROM row_flags,
        UNNEST(list_filter([
            CASE WHEN f_zero_v THEN {'ANOMALY_TYPE': 'ZERO_VOLTAGE', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ext_v_vr THEN {'ANOMALY_TYPE': 'EXTREME_VOLTAGE_VR', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ext_v_vy THEN {'ANOMALY_TYPE': 'EXTREME_VOLTAGE_VY', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ext_v_vb THEN {'ANOMALY_TYPE': 'EXTREME_VOLTAGE_VB', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ext_i_ir THEN {'ANOMALY_TYPE': 'EXTREME_CURRENT_IR', 'SEVERITY': 'CRITICAL'} END,
            CASE WHEN f_ext_i_iy THEN {'ANOMALY_TYPE': 'EXTREME_CURRENT_IY', 'SEVERITY': 'CRITICAL'} END,
            CASE WHEN f_ext_i_ib THEN {'ANOMALY_TYPE': 'EXTREME_CURRENT_IB', 'SEVERITY': 'CRITICAL'} END,
            CASE WHEN f_missing_kwh THEN {'ANOMALY_TYPE': 'MISSING_KWH', 'SEVERITY': 'MEDIUM'} END,
            CASE WHEN f_zero_kwh_load THEN {'ANOMALY_TYPE': 'ZERO_KWH_WITH_LOAD', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_neg_kvar_r THEN {'ANOMALY_TYPE': 'NEGATIVE_KVAR_KVAR_R', 'SEVERITY': 'LOW'} END,
            CASE WHEN f_neg_kvar_y THEN {'ANOMALY_TYPE': 'NEGATIVE_KVAR_KVAR_Y', 'SEVERITY': 'LOW'} END,
            CASE WHEN f_neg_kvar_b THEN {'ANOMALY_TYPE': 'NEGATIVE_KVAR_KVAR_B', 'SEVERITY': 'LOW'} END,
            CASE WHEN f_ploss_kw_r THEN {'ANOMALY_TYPE': 'PHASE_LOSS_KW_R', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ploss_kw_y THEN {'ANOMALY_TYPE': 'PHASE_LOSS_KW_Y', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ploss_kw_b THEN {'ANOMALY_TYPE': 'PHASE_LOSS_KW_B', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ploss_kvar_r THEN {'ANOMALY_TYPE': 'PHASE_LOSS_KVAR_R', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ploss_kvar_y THEN {'ANOMALY_TYPE': 'PHASE_LOSS_KVAR_Y', 'SEVERITY': 'HIGH'} END,
            CASE WHEN f_ploss_kvar_b THEN {'ANOMALY_TYPE': 'PHASE_LOSS_KVAR_B', 'SEVERITY': 'HIGH'} END
        ], x -> x IS NOT NULL)) AS u(rec)
    """)
    n_hard = con.execute("SELECT COUNT(*) FROM hard_anomalies").fetchone()[0]
    print(f"  Hard-rule anomalies flagged: {n_hard}")

    # clean: same nulling rules as the original Stage 6, now reading the
    # precomputed booleans from row_flags instead of re-evaluating the
    # conditions against base_month a second time.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE clean AS
        SELECT
            MTR_NO, DT_CODE_NEW, DT_CAT, DATE, TIME_SLOT,
            CASE WHEN f_zero_v THEN NULL WHEN f_ext_v_vr THEN NULL ELSE VR END AS VR,
            CASE WHEN f_zero_v THEN NULL WHEN f_ext_v_vy THEN NULL ELSE VY END AS VY,
            CASE WHEN f_zero_v THEN NULL WHEN f_ext_v_vb THEN NULL ELSE VB END AS VB,
            CASE WHEN f_ext_i_ir THEN NULL ELSE IR END AS IR,
            CASE WHEN f_ext_i_iy THEN NULL ELSE IY END AS IY,
            CASE WHEN f_ext_i_ib THEN NULL ELSE IB END AS IB,
            KW_R, KW_Y, KW_B,
            CASE WHEN f_neg_kvar_r THEN NULL ELSE KVAR_R END AS KVAR_R,
            CASE WHEN f_neg_kvar_y THEN NULL ELSE KVAR_Y END AS KVAR_Y,
            CASE WHEN f_neg_kvar_b THEN NULL ELSE KVAR_B END AS KVAR_B,
            KWH_TOTAL, ct_scaled,
            f_zero_v AS was_zero_voltage,
            meter_seq, dt_meter_rank, is_meter_replaced
        FROM row_flags
    """)
    n_clean = con.execute("SELECT COUNT(*) FROM clean").fetchone()[0]
    print(f"  Clean dataset rows: {n_clean}")


# ═══════════════════════════════════════════════════════════════════════
# STAGE 7: Export this month's outputs
# ═══════════════════════════════════════════════════════════════════════
def stage7_export(con, ym):
    exports = {
        "clean_data": "clean",
        "missing_slot_days": "missing_slot_days",
        "missing_slot_days_dt": "missing_slot_days_dt",   # DT-stitched coverage view
        "threshold_anomalies": "threshold_anomalies",
        "hard_anomalies": "hard_anomalies",
    }
    for name, table in exports.items():
        out_dir = os.path.join(OUTPUT_DIR, name)
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"month_{ym}.parquet")
        con.execute(f"""
            COPY (SELECT * FROM {table}) TO '{out_file}'
            (FORMAT PARQUET, COMPRESSION '{PARQUET_COMPRESSION}', ROW_GROUP_SIZE {EXPORT_ROW_GROUP_SIZE})
        """)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(STATUS_DIR, exist_ok=True)
    script_start = time.time()
    con = get_con()

    parquet_path = stage0_csv_to_parquet(con)
    stage1_load_master(con)

    months = [r[0] for r in con.execute(f"""
        SELECT DISTINCT SOURCE_YM FROM read_parquet('{parquet_path}/**/*.parquet', hive_partitioning=1)
        ORDER BY SOURCE_YM
    """).fetchall()]
    print(f"\nFound {len(months)} months to process: {months}\n")

    for i, ym in enumerate(months):
        marker = os.path.join(STATUS_DIR, f"{ym}.done")
        if os.path.exists(marker):
            with open(marker) as f:
                cached_logic_version = f.read().strip()
            if cached_logic_version == LOGIC_VERSION:
                print(f"=== MONTH {ym} ({i+1}/{len(months)}) -- already done at logic v{LOGIC_VERSION}, skipping ===")
                continue
            else:
                print(f"=== MONTH {ym} ({i+1}/{len(months)}) -- was processed at logic v{cached_logic_version}, "
                      f"code now at v{LOGIC_VERSION} -- reprocessing (cheap: reuses cached raw Parquet, "
                      f"skips Stage 0) ===")

        t0 = time.time()
        print(f"=== MONTH {ym} ({i+1}/{len(months)}) ===")
        try:
            month_start, month_end = build_month_extended(con, ym, parquet_path)
            stage3_missing_slots(con)
            stage4_rolling_threshold_anomalies(con, month_start, month_end)
            stage56_hard_anomalies_and_clean(con)
            stage7_export(con, ym)
            for t in ("scaled_ext", "base_month", "base_month_dt", "daily_slot_counts",
                      "daily_slot_counts_dt", "meter_expected_slots", "dt_expected_slots",
                      "missing_slot_days", "missing_slot_days_dt", "with_thresholds",
                      "threshold_anomalies", "row_flags", "hard_anomalies", "clean"):
                con.execute(f"DROP TABLE IF EXISTS {t}")
            try:
                con.execute("CHECKPOINT")
            except Exception:
                pass   
            with open(marker, "w") as f:
                f.write(LOGIC_VERSION)
            print(f"  Month {ym} done in {time.time()-t0:.1f}s\n")
        except Exception as e:
            print(f"  MONTH {ym} FAILED: {e}")
            print(f"  Rerun the script -- months before {ym} are marked done and will be skipped.")
            raise

    print(f"\nPIPELINE COMPLETE. Total time: {time.time() - script_start:.1f}s")
    print(f"Outputs in: {OUTPUT_DIR}")
    print("Query the full dataset across all months with, e.g.:")
    print(f"  SELECT * FROM read_parquet('{os.path.join(OUTPUT_DIR, 'clean_data')}/*.parquet')")


if __name__ == "__main__":
    main()