"""
DT HEALTH REPORT
===================================================================
Computes a per-DT "clean data %" and lists DTs at or above a threshold
(default 50%), WITH a breakdown of which anomaly types are actually
driving the bad rows for each DT.

DEFINITION:
    health % = (present_readings - bad_readings) / present_readings

  - present_readings : rows that actually exist in clean_data for that DT
  - bad_readings     : distinct (meter, date, time_slot) readings flagged in
                        EITHER threshold_anomalies OR hard_anomalies
  - anomaly_type_breakdown : for each DT, every anomaly type that hit it
                        and how many times, e.g.
                        "ZERO_VOLTAGE:120; VR_HIGH:45; EXTREME_CURRENT_IR:10"
                        sorted most-frequent-first. Threshold anomalies are
                        labeled PARAMETER_DIRECTION (e.g. VR_HIGH, IY_LOW);
                        hard-rule anomalies keep their own ANOMALY_TYPE
                        (ZERO_VOLTAGE, EXTREME_VOLTAGE_VR, EXTREME_CURRENT_IR).
                        NOTE: this counts TYPE OCCURRENCES, not distinct rows
                        -- one bad row can carry more than one anomaly type
                        (e.g. flagged by both a hard bound AND the rolling
                        window), so these counts can exceed bad_rows.

This is purely a data-quality measure -- missing/offline slots are NOT
counted against a DT here. A DT with very few readings but all of them
clean will still score 100%; if you also want to penalize DTs with a lot
of missing data (low uptime), that's a separate, addable metric.

NOTE: if the full 67-month pipeline run hasn't finished yet, this will only
reflect whatever months have completed so far (globs pick up whatever
month_*.parquet files currently exist) -- rerun after the full run finishes
for the complete picture.

Usage:
  py -3.12 dt_health_report.py
"""

import duckdb
import os

OUTPUT_DIR = r"C:\Users\Siddharth Jain\Documents\BRPL DTS\pipeline_output"
HEALTH_THRESHOLD_PCT = 50.0   # changed from 75.0 -- change this to whatever cutoff you want

con = duckdb.connect()

clean_glob = os.path.join(OUTPUT_DIR, "clean_data", "*.parquet").replace("\\", "/")
threshold_glob = os.path.join(OUTPUT_DIR, "threshold_anomalies", "*.parquet").replace("\\", "/")
hard_glob = os.path.join(OUTPUT_DIR, "hard_anomalies", "*.parquet").replace("\\", "/")

query = f"""
WITH all_readings AS (
    SELECT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT
    FROM read_parquet('{clean_glob}')
),
bad_threshold AS (
    SELECT DISTINCT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT
    FROM read_parquet('{threshold_glob}')
),
bad_hard AS (
    SELECT DISTINCT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT
    FROM read_parquet('{hard_glob}')
),
per_dt AS (
    SELECT
        a.DT_CODE_NEW,
        COUNT(*) AS present_rows,
        COUNT(*) FILTER (WHERE bt.DT_CODE_NEW IS NOT NULL OR bh.DT_CODE_NEW IS NOT NULL) AS bad_rows
    FROM all_readings a
    LEFT JOIN bad_threshold bt USING (DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT)
    LEFT JOIN bad_hard bh USING (DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT)
    GROUP BY a.DT_CODE_NEW
),
-- every anomaly TYPE occurrence per DT, from both sources, unified into one label
anomaly_types AS (
    SELECT DT_CODE_NEW, ANOMALY_TYPE
    FROM read_parquet('{hard_glob}')

    UNION ALL

    SELECT DT_CODE_NEW, PARAMETER || '_' || DIRECTION AS ANOMALY_TYPE
    FROM read_parquet('{threshold_glob}')
),
anomaly_counts AS (
    SELECT DT_CODE_NEW, ANOMALY_TYPE, COUNT(*) AS n
    FROM anomaly_types
    GROUP BY DT_CODE_NEW, ANOMALY_TYPE
),
anomaly_breakdown AS (
    SELECT
        DT_CODE_NEW,
        STRING_AGG(ANOMALY_TYPE || ':' || n, '; ' ORDER BY n DESC) AS anomaly_type_breakdown,
        MAX(CASE WHEN rn = 1 THEN ANOMALY_TYPE END) AS top_anomaly_type
    FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY DT_CODE_NEW ORDER BY n DESC) AS rn
        FROM anomaly_counts
    )
    GROUP BY DT_CODE_NEW
)
SELECT
    p.DT_CODE_NEW,
    p.present_rows,
    p.bad_rows,
    p.present_rows - p.bad_rows AS clean_rows,
    ROUND(100.0 * (p.present_rows - p.bad_rows) / NULLIF(p.present_rows, 0), 1) AS health_pct,
    COALESCE(ab.top_anomaly_type, '') AS top_anomaly_type,
    COALESCE(ab.anomaly_type_breakdown, '') AS anomaly_type_breakdown
FROM per_dt p
LEFT JOIN anomaly_breakdown ab USING (DT_CODE_NEW)
WHERE health_pct >= {HEALTH_THRESHOLD_PCT}
ORDER BY health_pct DESC
"""

result = con.execute(query).fetchdf()
out_csv = os.path.join(OUTPUT_DIR, "dt_health_report.csv")
result.to_csv(out_csv, index=False)

print(f"DTs at or above {HEALTH_THRESHOLD_PCT}% clean: {len(result)}")
print(f"Full list saved to: {out_csv}")
print()
print(result.head(20).to_string(index=False))