"""
fl_config.py -- Shared configuration for the BRPL DTS Streamlit dashboard.

Single source of truth for paths and column names. Every other dashboard
module imports from here instead of hardcoding strings, so a schema change
only needs to happen in one place.

IMPORTANT: these paths/column names must match dt_pipeline.py exactly --
this dashboard reads the pipeline's Parquet OUTPUTS, it does not recompute
anything the pipeline already computed.
"""

import os

# ---------------------------------------------------------------------------
# PATHS -- must match OUTPUT_DIR in dt_pipeline.py
# ---------------------------------------------------------------------------
PIPELINE_OUTPUT_DIR = r"D:\New dashboard code\DT DATA DASHBOARD V1\pipeline_output"

CLEAN_DATA_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "clean_data")
RAW_DATA_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "raw_parquet")
HARD_ANOMALIES_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "hard_anomalies")
THRESHOLD_ANOMALIES_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "threshold_anomalies")
MISSING_SLOT_DAYS_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "missing_slot_days")
MISSING_SLOT_DAYS_DT_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "missing_slot_days_dt")
STATUS_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "month_status")

MASTER_CACHE = os.path.join(PIPELINE_OUTPUT_DIR, "master_cache.parquet")
EARLIEST_MASTER_CACHE = os.path.join(PIPELINE_OUTPUT_DIR, "earliest_master_cache.parquet")
METER_SEQ_CACHE = os.path.join(PIPELINE_OUTPUT_DIR, "meter_seq_cache.parquet")

# For RAW sustained loading: join raw_data with master_cache to get DT assignments
RAW_DATA_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "raw_parquet")

# Dashboard's own derived-cache folder (small pre-aggregated tables this
# dashboard builds, separate from the pipeline's own caches above).
DASH_CACHE_DIR = os.path.join(PIPELINE_OUTPUT_DIR, "dashboard_cache")
HIERARCHY_CACHE = os.path.join(DASH_CACHE_DIR, "hierarchy.parquet")
GRANULARITY_CACHE = os.path.join(DASH_CACHE_DIR, "meter_granularity.parquet")

# DuckDB spill-to-disk directory for the dashboard's own connection, kept
# separate from the pipeline's own duckdb_tmp so the two processes never
# fight over the same temp files if both happen to run at once.
DASH_DUCKDB_TMP = os.path.join(PIPELINE_OUTPUT_DIR, "dashboard_duckdb_tmp")
CACHE_META = os.path.join(DASH_CACHE_DIR, "cache_meta.txt")   # tracks which months the cache was built from

# ---------------------------------------------------------------------------
# COLUMN NAMES -- must match clean_data / master_cache schema exactly.
# If your pipeline's actual column names differ, change ONLY this block.
# ---------------------------------------------------------------------------
COL_METER = "MTR_NO"
COL_DT = "DT_CODE_NEW"
COL_DT_RAW = "SOURCE_YM"          # DT code in raw_parquet files (different from clean_data)
COL_FL = "SDO_CD"                 # FL = Feeder/Field Location, stored as SDO_CD upstream
COL_DT_CAT = "DT_CAT"
COL_DATE = "DATE"
COL_DATE_RAW = "OCCUR_D_RAW"      # Date column in raw_parquet files (different from clean_data)
COL_TIME_SLOT = "TIME_SLOT"
COL_KVA_RATING = "KVA_RATING"
COL_CT_RATIO = "CT_RATIO"
COL_METER_SEQ = "meter_seq"
COL_IS_METER_REPLACED = "is_meter_replaced"

VOLT_COLS = ["VR", "VY", "VB"]
CURR_COLS = ["IR", "IY", "IB"]
KW_COLS = ["KW_R", "KW_Y", "KW_B"]
KVAR_COLS = ["KVAR_R", "KVAR_Y", "KVAR_B"]
ENERGY_COL = "KWH_TOTAL"

# ---------------------------------------------------------------------------
# KVA FORMULA CONSTANTS
# ---------------------------------------------------------------------------
# 30-min (standard) meters: KVA from total KW/KVAR across phases.
KVA_EXPR_30MIN = "SQRT(POWER(KW_R + KW_Y + KW_B, 2) + POWER(KVAR_R + KVAR_Y + KVAR_B, 2))"
# 15-min (smart) meters, mostly Jan-2026 onward: KVA from per-phase V*I, /1000 for kVA.
KVA_EXPR_15MIN = "(VR * IR + VY * IY + VB * IB) / 1000.0"
# A meter's expected slots/day > this threshold is treated as 15-min cadence (96/day vs 48/day).
SLOT_COUNT_15MIN_THRESHOLD = 60

# ---------------------------------------------------------------------------
# SUSTAINED LOADING BANDS (percent of rated KVA)
# ---------------------------------------------------------------------------
LOADING_BANDS = [
    ("70-80%", 0.70, 0.80),
    ("80-90%", 0.80, 0.90),
    ("90-100%", 0.90, 1.00),
    ("100-110%", 1.00, 1.10),
    ("110-120%", 1.10, 1.20),
    (">120%", 1.20, None),
]
SUSTAINED_MIN_HOURS_PER_MONTH = 30

# ---------------------------------------------------------------------------
# INTERPOLATION RULES (Load Curve tab, and the fleet-wide clean-KVA cache)
# ---------------------------------------------------------------------------
INTERP_LINEAR_MAX_GAP = 2        # 1-2 consecutive missing slots -> linear
INTERP_QUADRATIC_MAX_GAP = 4     # 3-4 consecutive missing slots -> quadratic
# 5+ consecutive missing slots -> cubic
