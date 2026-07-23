"""
Full raw parameter export for a given SDO, month-wise - DuckDB + Parquet edition
====================================================================================
For every raw meter reading in Jan-Jul 2026 whose meter belongs to SDO 2652
(per the master's MTR_NO -> DT_CODE_NEW -> SDO_CD mapping), export the full
set of requested raw parameters, tagged with DT_CODE_NEW, SDO_CD, and MONTH.

Output columns:
    MONTH, SDO_CD, DT_CODE_NEW, MTR_NO, OCCUR_DATE, OCCUR_D, OCCUR_T,
    V, VR, VY, VB, IR, IY, IB, KWH_TOTAL, KW_R, KW_Y, KW_B, KVAR_R, KVAR_Y, KVAR_B

Usage:
    py -3.12 dt_raw_detail_sdo2652.py

Caching:
  - Master parquet cache: pipeline_output/master_cache.parquet (reused if present -
    delete it if the master workbook changes).
  - Raw detail parquet cache: pipeline_output/raw_detail_parquet/<MONTH>.parquet
    (reused if present - delete a month's file to force reconversion of that month).
"""

from pathlib import Path
import re
import duckdb
import pandas as pd

# ── CONFIG ──────────────────────────────────────────────────────────────
MASTER_FILE = Path("csv 2021-2026/csv_master 2026._3xlsb.xlsb")
MASTER_CACHE = Path("pipeline_output/master_cache.parquet")

RAW_FOLDER = Path("Raw Files monthly")
RAW_DETAIL_PARQUET_DIR = Path("pipeline_output/raw_detail_parquet")

SDO_CODE = "2652"
MONTH_FILTER = [202601, 202602, 202603, 202604, 202605, 202606, 202607]

OUTPUT_DIR = Path("sdo_2652_monthly_detail")

DT_COL = "DT_CODE_NEW"
SDO_COL = "SDO_CD"
MTR_COL = "MTR_NO"
MTR_ALIASES = ["MTR_NO", "METER_NO", "MTR NO", "METER NO", "DT_METER_NO", "DT METER NO"]

# The raw parameter columns to keep, in output order (excluding MTR_NO, which is always kept)
RAW_PARAM_COLS = [
    "OCCUR_DATE", "OCCUR_D", "OCCUR_T",
    "V", "VR", "VY", "VB",
    "IR", "IY", "IB",
    "KWH_TOTAL",
    "KW_R", "KW_Y", "KW_B",
    "KVAR_R", "KVAR_Y", "KVAR_B",
]
# ─────────────────────────────────────────────────────────────────────────


def find_col(columns, aliases):
    norm_map = {re.sub(r"[\s_]+", "", c).upper(): c for c in columns}
    for alias in aliases:
        key = re.sub(r"[\s_]+", "", alias).upper()
        if key in norm_map:
            return norm_map[key]
    return None


def month_from_filename(path: Path) -> int:
    m = re.search(r"(20\d{4})", path.stem)
    if not m:
        raise ValueError(f"Could not infer month (YYYYMM) from filename: {path.name}")
    return int(m.group(1))


def ensure_master_parquet():
    if MASTER_CACHE.exists():
        print(f"Using cached master parquet: {MASTER_CACHE}")
        return

    print(f"Building master parquet cache from: {MASTER_FILE}")
    xls = pd.ExcelFile(MASTER_FILE, engine="pyxlsb")
    frames = [pd.read_excel(MASTER_FILE, engine="pyxlsb", sheet_name=s) for s in xls.sheet_names]
    master = pd.concat(frames, ignore_index=True)
    print(f"  master rows: {len(master):,}")

    dt_col = find_col(master.columns, [DT_COL])
    sdo_col = find_col(master.columns, [SDO_COL])
    mtr_col = find_col(master.columns, MTR_ALIASES)
    if not all([dt_col, sdo_col, mtr_col]):
        raise ValueError(
            f"Could not find expected columns in master.\n"
            f"  DT: {dt_col}  SDO: {sdo_col}  MTR: {mtr_col}\n"
            f"  Available columns: {list(master.columns)}"
        )

    master = master.rename(columns={dt_col: DT_COL, sdo_col: SDO_COL, mtr_col: MTR_COL})
    master[SDO_COL] = master[SDO_COL].astype(str).str.strip().str.upper()
    master[DT_COL] = master[DT_COL].astype(str).str.strip().str.upper()
    master[MTR_COL] = master[MTR_COL].astype(str).str.strip().str.upper()

    lookup = master[[MTR_COL, DT_COL, SDO_COL]].drop_duplicates()

    # A meter should map to exactly ONE DT/SDO. If it matches more than one distinct
    # combo, the join would multiply every raw reading for that meter - force exactly
    # one row per meter (first, after sorting) and flag the rest for manual review.
    dupe_meters = lookup[lookup.duplicated(subset=[MTR_COL], keep=False)]
    if not dupe_meters.empty:
        n = dupe_meters[MTR_COL].nunique()
        ambiguous_path = MASTER_CACHE.parent / "ambiguous_meters_multiple_dt.csv"
        MASTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        dupe_meters.sort_values(MTR_COL).to_csv(ambiguous_path, index=False)
        print(f"  !! WARNING: {n} meter(s) match MULTIPLE DT/SDO combos in master - "
              f"this was causing the row-count blowup. Keeping only the first combo "
              f"per meter; full list of ambiguous meters saved to {ambiguous_path} "
              f"for manual review.")

    lookup = lookup.sort_values([MTR_COL, DT_COL, SDO_COL]).drop_duplicates(subset=[MTR_COL], keep="first")

    MASTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    lookup.to_parquet(MASTER_CACHE, index=False)
    print(f"  saved: {MASTER_CACHE}  ({len(lookup):,} unique meters)")


def ensure_raw_detail_parquet():
    RAW_DETAIL_PARQUET_DIR.mkdir(parents=True, exist_ok=True)

    all_files = sorted(p for p in RAW_FOLDER.iterdir() if p.suffix.lower() == ".csv")
    target_files = [p for p in all_files if month_from_filename(p) in MONTH_FILTER]

    if not target_files:
        raise FileNotFoundError(f"No raw CSVs matching MONTH_FILTER={MONTH_FILTER} in {RAW_FOLDER}")

    con = duckdb.connect()
    for path in target_files:
        month = month_from_filename(path)
        out_path = RAW_DETAIL_PARQUET_DIR / f"{month}.parquet"
        if out_path.exists():
            print(f"  [cached] {month} -> {out_path.name}")
            continue

        header_df = pd.read_csv(path, nrows=0)
        actual_cols = list(header_df.columns)

        mtr_col = find_col(actual_cols, MTR_ALIASES)
        if not mtr_col:
            print(f"  !! SKIPPED {path.name} - no MTR_NO-like column. Columns: {actual_cols}")
            continue

        # Build a SELECT list: use the real column if present, otherwise NULL AS <name>
        select_parts = [f'"{mtr_col}"::VARCHAR AS {MTR_COL}']
        missing = []
        for col in RAW_PARAM_COLS:
            actual = find_col(actual_cols, [col])
            if actual:
                select_parts.append(f'"{actual}"::VARCHAR AS {col}')
            else:
                select_parts.append(f'NULL AS {col}')
                missing.append(col)

        if missing:
            print(f"  (note: {path.name} is missing columns {missing} - filled with NULL)")

        select_sql = ",\n                ".join(select_parts)
        print(f"  converting {path.name} -> {out_path.name}")
        con.execute(f"""
            COPY (
                SELECT DISTINCT
                {select_sql},
                {month} AS MONTH
                FROM read_csv_auto('{path.as_posix()}', ALL_VARCHAR=TRUE)
            ) TO '{out_path.as_posix()}' (FORMAT PARQUET)
        """)
    con.close()


def main():
    ensure_master_parquet()
    print(f"Preparing raw detail parquet cache from: {RAW_FOLDER}")
    ensure_raw_detail_parquet()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    param_select = ",\n            ".join(f"raw.{c}" for c in RAW_PARAM_COLS)

    con = duckdb.connect()

    # register the master-for-SDO subset once, reused across months
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW master_sdo AS
        SELECT * FROM read_parquet('{MASTER_CACHE.as_posix()}')
        WHERE {SDO_COL} = '{SDO_CODE}'
    """)

    for month in MONTH_FILTER:
        month_parquet = RAW_DETAIL_PARQUET_DIR / f"{month}.parquet"
        if not month_parquet.exists():
            print(f"  !! No parquet for MONTH {month} - skipping (file missing or was skipped earlier)")
            continue

        out_path = OUTPUT_DIR / f"dt_raw_detail_sdo_2652_{month}.csv"

        query = f"""
            SELECT
                raw.MONTH,
                master_sdo.{SDO_COL},
                master_sdo.{DT_COL},
                raw.{MTR_COL},
                {param_select}
            FROM read_parquet('{month_parquet.as_posix()}') AS raw
            JOIN master_sdo ON raw.{MTR_COL} = master_sdo.{MTR_COL}
            ORDER BY master_sdo.{DT_COL}, raw.{MTR_COL}
        """
        result = con.execute(query).df()

        print(f"MONTH {month}: {len(result):,} rows | "
              f"{result[DT_COL].nunique():,} DTs | {result[MTR_COL].nunique():,} meters")

        result.to_csv(out_path, index=False)
        print(f"  saved: {out_path.resolve()}")

    con.close()
    print(f"\nAll monthly files saved under: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()