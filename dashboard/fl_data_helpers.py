"""
fl_data_helpers.py -- Shared DuckDB connection, Parquet glob helpers, and
pre-aggregated cache builders (FL->DT->Meter hierarchy, meter granularity)
used by every tab and engine module in this dashboard.

THIS FILE WAS MISSING FROM YOUR UPLOAD (the file with this name in your
upload was actually a different, old, single-file dashboard -- not a data
helper module). Everything below is written fresh to match how every
other module in this bundle calls it.

CACHING STRATEGY (per your choice: pre-aggregate once at startup, not a
live DuckDB query on every filter change): every expensive result here is
wrapped in @st.cache_data / @st.cache_resource, keyed off a signature of
which months are currently present in clean_data/. That means:
  - First load after a fresh pipeline run: slower (builds everything).
  - Every filter change after that: instant (reads the cached DataFrame).
  - If dt_pipeline.py adds a new month and you reload the dashboard: the
    month signature changes, so the caches rebuild automatically -- you
    don't need to manually clear anything.

SCHEMA ASSUMPTION -- please check this against your real pipeline output:
  list_available_months() assumes clean_data/ has one file (or one
  Hive-style partition) per month, with a YYYYMM digit sequence somewhere
  in the path/filename (e.g. clean_data/202601.parquet, or
  clean_data/YM=202601/part-0.parquet). If your pipeline lays clean_data
  out differently (e.g. one giant file, or partitioned by year only),
  this month-detection logic needs a one-line tweak -- tell me the actual
  layout and I'll adjust it.
"""

import os
import glob
import duckdb
import pandas as pd
import streamlit as st

import fl_config as cfg


# ---------------------------------------------------------------------------
# DUCKDB CONNECTION -- one connection per Streamlit session, reused for
# every query (st.cache_resource, not st.cache_data, since a DB connection
# isn't a picklable "data" value).
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _make_con():
    os.makedirs(cfg.DASH_DUCKDB_TMP, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("PRAGMA threads=4")
        # Memory-bounded, disk-spilling connection -- mirrors the pipeline's
        # own safe defaults. Without this, a query whose result doesn't fit
        # in RAM (e.g. scanning the whole fleet's clean_data at once) can
        # crash the Streamlit process with a MemoryError instead of
        # spilling to disk.
        con.execute("PRAGMA memory_limit='6GB'")
        con.execute(f"PRAGMA temp_directory='{cfg.DASH_DUCKDB_TMP.replace(chr(92), '/')}'")
        con.execute("SET preserve_insertion_order=false")
    except Exception:
        pass
    return con


def get_con():
    """Returns the shared DuckDB connection, self-healing if a previous query
    was interrupted mid-flight (e.g. clicking Streamlit's Stop button while a
    long query was still running). That failure mode leaves the connection
    permanently stuck -- every later query fails with 'Attempting to execute
    an unsuccessful or closed pending query result' -- and since the
    connection is a cached resource, it otherwise survives script reruns
    indefinitely, forcing a full app restart to recover. A cheap health-check
    query here detects that and transparently rebuilds the connection instead."""
    con = _make_con()
    try:
        con.execute("SELECT 1")
    except Exception:
        _make_con.clear()
        con = _make_con()
    return con


# ---------------------------------------------------------------------------
# PARQUET GLOB HELPERS
# ---------------------------------------------------------------------------
def _glob(dir_path):
    if not os.path.isdir(dir_path):
        return []
    return sorted(glob.glob(os.path.join(dir_path, "**", "*.parquet"), recursive=True))


def clean_data_glob():
    return _glob(cfg.CLEAN_DATA_DIR)


def raw_data_glob():
    return _glob(cfg.RAW_DATA_DIR)


def hard_anomalies_glob():
    return _glob(cfg.HARD_ANOMALIES_DIR)


def threshold_anomalies_glob():
    return _glob(cfg.THRESHOLD_ANOMALIES_DIR)


def missing_slot_days_glob():
    return _glob(cfg.MISSING_SLOT_DAYS_DIR)


def missing_slot_days_dt_glob():
    return _glob(cfg.MISSING_SLOT_DAYS_DT_DIR)


def _globs_literal(paths):
    """Turn a list of file paths into a DuckDB SQL array literal, e.g.
    read_parquet(['a.parquet','b.parquet']). Returns '[]' (empty array
    literal) if there are no files -- callers check for this string to
    short-circuit rather than firing a query against zero files."""
    if not paths:
        return "[]"
    escaped = [p.replace("\\", "/").replace("'", "''") for p in paths]
    return "[" + ", ".join(f"'{p}'" for p in escaped) + "]"


# ---------------------------------------------------------------------------
# DISK-BACKED CACHE (survives Streamlit process restarts) -- @st.cache_data
# only lives inside one running Python process, so every `streamlit run`
# restart previously recomputed hierarchy / granularity / monthly-peak-KVA /
# sustained-loading from scratch, even though each builder already wrote its
# result to a Parquet file in DASH_CACHE_DIR. These two helpers close that
# gap: before running an expensive rebuild, check whether a fresh on-disk
# cache (matching the current month signature) already exists, and use it
# instead. A rebuild is only ever needed the FIRST time a given set of
# months has been seen, not on every process restart in between.
# ---------------------------------------------------------------------------
# Current cache format version — bump this any time the cache schema changes
# so old (wrong) caches are invalidated and rebuilt automatically.
CACHE_VERSION = "v2"

def _read_disk_cache_if_fresh(path, months_sig):
    meta_path = path + ".meta"
    if os.path.exists(path) and os.path.exists(meta_path):
        try:
            with open(meta_path, "r") as f:
                saved_sig = f.read().strip()
            # Include CACHE_VERSION in the comparison so schema changes
            # (e.g. new clean KVA algorithm) force a rebuild
            expected_sig = f"{CACHE_VERSION}:{months_sig}"
            if saved_sig == expected_sig:
                return pd.read_parquet(path)
        except Exception:
            pass
    return None


def _write_disk_cache(path, months_sig, df):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_parquet(path, index=False)
        expected_sig = f"{CACHE_VERSION}:{months_sig}"
        with open(path + ".meta", "w") as f:
            f.write(expected_sig)
    except Exception:
        pass  # disk cache is a speed optimization, never fatal if it fails


# ---------------------------------------------------------------------------
# METER ID NORMALIZATION -- master_cache.parquet's meter IDs come from Excel
# workbooks read via pandas. A purely-numeric meter ID (e.g. 29505765) very
# often gets read by pandas as a float64 cell (especially if any other cell
# in that column is blank), so a plain .astype(str) produces '29505765.0'
# -- a value that will never match the same meter's ID as read from the
# raw CSVs in clean_data (those are cast directly to VARCHAR in the
# pipeline, no float round-trip involved). This strips that specific
# artifact. Alphanumeric meter IDs (e.g. '2510DDK-GK2127A-1') never match
# the trailing '.0' pattern, so they pass through completely untouched --
# safe on a mixed numeric/alphanumeric meter ID column.
# ---------------------------------------------------------------------------
def _normalize_meter_id(series):
    s = series.astype(str).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)
    return s


# ---------------------------------------------------------------------------
# MONTH SIGNATURE -- the cache key. list_available_months() is itself cheap
# (just a directory listing), so it's fine to call every rerun; the
# EXPENSIVE builders below are keyed off its result and only re-execute
# when the signature actually changes.
# ---------------------------------------------------------------------------
def list_available_months():
    months = set()
    for p in clean_data_glob():
        digits = "".join(ch for ch in os.path.basename(p) if ch.isdigit())
        for i in range(max(0, len(digits) - 5)):
            chunk = digits[i:i + 6]
            if len(chunk) == 6 and chunk.startswith(("19", "20")):
                months.add(chunk)
                break
    return sorted(months)


def _months_signature(months):
    return ",".join(months) if months else "none"


def _files_for_month(paths, ym):
    """Filter a list of file paths down to just those belonging to month ym
    (a 6-digit YYYYMM string), using the same digit-scan convention as
    list_available_months(). Lets a chunked, one-month-at-a-time
    computation touch only the relevant file(s) instead of scanning
    every month's data on every iteration."""
    matches = []
    for p in paths:
        # Check both basename and directory name for month digits
        # (raw files use directory names like SOURCE_YM=202101)
        basename = os.path.basename(p)
        dirname = os.path.basename(os.path.dirname(p))
        
        for name in [basename, dirname]:
            digits = "".join(ch for ch in name if ch.isdigit())
            for i in range(max(0, len(digits) - 5)):
                if digits[i:i + 6] == ym:
                    matches.append(p)
                    break
            if p in matches:
                break
    return matches


# ---------------------------------------------------------------------------
# METER GRANULARITY -- 15-min (smart, mostly Jan-2026+) vs 30-min (legacy).
# Detected empirically per meter (avg slots/day) rather than assumed from a
# date cutoff, since rollout timing varies meter to meter in practice.
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _build_meter_granularity(_months_sig):
    cached = _read_disk_cache_if_fresh(cfg.GRANULARITY_CACHE, _months_sig)
    if cached is not None:
        return cached

    con = get_con()
    globs = _globs_literal(clean_data_glob())
    if globs == "[]":
        return None
    df = con.execute(f"""
        WITH per_day AS (
            SELECT {cfg.COL_METER} AS MTR_NO, {cfg.COL_DATE} AS DATE, COUNT(*) AS N_SLOTS
            FROM read_parquet({globs})
            GROUP BY 1, 2
        )
        SELECT MTR_NO, AVG(N_SLOTS) AS AVG_SLOTS_PER_DAY
        FROM per_day
        GROUP BY MTR_NO
    """).df()
    if df.empty:
        return df
    df["SLOT_MINUTES"] = df["AVG_SLOTS_PER_DAY"].apply(
        lambda n: 15 if n > cfg.SLOT_COUNT_15MIN_THRESHOLD else 30
    )
    result = df[["MTR_NO", "SLOT_MINUTES"]]
    _write_disk_cache(cfg.GRANULARITY_CACHE, _months_sig, result)
    return result


def get_meter_granularity_cached():
    months = list_available_months()
    return _build_meter_granularity(_months_signature(months))


def register_granularity_table(con, granularity_df):
    """Registers the granularity DataFrame as a DuckDB temp table named
    'meter_granularity' so every SQL query in this bundle can LEFT JOIN
    against it by MTR_NO."""
    if granularity_df is None or granularity_df.empty:
        granularity_df = pd.DataFrame({"MTR_NO": pd.Series(dtype="object"), "SLOT_MINUTES": pd.Series(dtype="int64")})
    con.register("meter_granularity_src", granularity_df)
    con.execute("CREATE OR REPLACE TEMP TABLE meter_granularity AS SELECT * FROM meter_granularity_src")


# ---------------------------------------------------------------------------
# FL -> DT -> METER HIERARCHY -- small table (one row per meter-DT pair),
# built once from master_cache.parquet + meter_seq_cache.parquet.
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _build_hierarchy(_months_sig):
    cached = _read_disk_cache_if_fresh(cfg.HIERARCHY_CACHE, _months_sig)
    if cached is not None:
        return cached

    if not os.path.exists(cfg.MASTER_CACHE):
        return None
    master = pd.read_parquet(cfg.MASTER_CACHE)

    # Tolerate the master's original column names if they don't already
    # match the canonical names in fl_config (e.g. DT_METER_NO -> MTR_NO).
    rename_map = {}
    alias_map = {
        cfg.COL_METER: [cfg.COL_METER, "DT_METER_NO", "METER_NO"],
        cfg.COL_DT: [cfg.COL_DT],
        cfg.COL_FL: [cfg.COL_FL],
    }
    for canon, aliases in alias_map.items():
        if canon in master.columns:
            continue
        for a in aliases:
            if a in master.columns:
                rename_map[a] = canon
                break
    master = master.rename(columns=rename_map)

    required = [cfg.COL_METER, cfg.COL_DT, cfg.COL_FL]
    missing = [c for c in required if c not in master.columns]
    if missing:
        st.error(f"master_cache.parquet is missing required column(s): {missing}. "
                 f"Available columns: {list(master.columns)}")
        return None

    keep_cols = [c for c in [cfg.COL_METER, cfg.COL_DT, cfg.COL_FL, cfg.COL_KVA_RATING,
                              cfg.COL_CT_RATIO, "DT_CAT", "DT_STS"] if c in master.columns]
    hierarchy = master[keep_cols].drop_duplicates(subset=[cfg.COL_METER, cfg.COL_DT]).copy()

    for c in [cfg.COL_METER, cfg.COL_DT, cfg.COL_FL]:
        if c == cfg.COL_METER:
            hierarchy[c] = _normalize_meter_id(hierarchy[c])
        else:
            hierarchy[c] = hierarchy[c].astype(str).str.strip()

    # meter_seq: order in which a DT's meters were installed (for
    # meter-replacement history). Falls back to a constant if the cache
    # file/columns aren't present -- every meter just ranks as #1 then.
    if os.path.exists(cfg.METER_SEQ_CACHE):
        try:
            seq = pd.read_parquet(cfg.METER_SEQ_CACHE)
            seq_rename = {}
            if cfg.COL_METER not in seq.columns:
                for a in ["DT_METER_NO", "METER_NO"]:
                    if a in seq.columns:
                        seq_rename[a] = cfg.COL_METER
                        break
            seq = seq.rename(columns=seq_rename)
            join_cols = [c for c in [cfg.COL_METER, cfg.COL_DT] if c in seq.columns]
            if cfg.COL_METER_SEQ in seq.columns and join_cols:
                for c in join_cols:
                    if c == cfg.COL_METER:
                        seq[c] = _normalize_meter_id(seq[c])
                    else:
                        seq[c] = seq[c].astype(str).str.strip()
                hierarchy = hierarchy.merge(
                    seq[join_cols + [cfg.COL_METER_SEQ]].drop_duplicates(),
                    on=join_cols, how="left",
                )
        except Exception as e:
            st.warning(f"Could not read meter_seq_cache.parquet ({e}); "
                       f"meter-replacement ordering will default to 1 for every meter.")

    if cfg.COL_METER_SEQ not in hierarchy.columns:
        hierarchy[cfg.COL_METER_SEQ] = 1
    hierarchy[cfg.COL_METER_SEQ] = hierarchy[cfg.COL_METER_SEQ].fillna(1)

    hierarchy = hierarchy.sort_values([cfg.COL_DT, cfg.COL_METER_SEQ])
    hierarchy["dt_meter_rank"] = hierarchy.groupby(cfg.COL_DT).cumcount() + 1

    n_meters_per_dt = hierarchy.groupby(cfg.COL_DT)[cfg.COL_METER].transform("nunique")
    hierarchy[cfg.COL_IS_METER_REPLACED] = n_meters_per_dt > 1

    result = hierarchy.reset_index(drop=True)
    _write_disk_cache(cfg.HIERARCHY_CACHE, _months_sig, result)
    return result


def get_hierarchy_cached():
    months = list_available_months()
    cached = _read_disk_cache_if_fresh(cfg.HIERARCHY_CACHE, _months_signature(months))
    if cached is not None:
        return cached
    # Auto-build hierarchy cache if it doesn't exist
    return _build_hierarchy(_months_signature(months))
