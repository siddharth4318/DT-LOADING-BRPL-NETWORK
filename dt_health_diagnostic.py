import duckdb
import os

OUTPUT_DIR = r"C:\Users\Siddharth Jain\Documents\BRPL DTS\pipeline_output"
clean_glob = os.path.join(OUTPUT_DIR, "clean_data", "*.parquet").replace("\\", "/")
threshold_glob = os.path.join(OUTPUT_DIR, "threshold_anomalies", "*.parquet").replace("\\", "/")
hard_glob = os.path.join(OUTPUT_DIR, "hard_anomalies", "*.parquet").replace("\\", "/")

con = duckdb.connect()

# 1. True denominator -- how many distinct DTs exist in the dataset at all?
total_dts = con.execute(f"""
    SELECT COUNT(DISTINCT DT_CODE_NEW) FROM read_parquet('{clean_glob}')
""").fetchone()[0]
print(f"Total distinct DTs in dataset: {total_dts:,}")

# 2. Health % distribution (not just the >=75% count) -- is it bimodal
#    (mostly near 100%, small chronic-bad cluster) or spread out evenly?
dist = con.execute(f"""
    WITH all_readings AS (
        SELECT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT FROM read_parquet('{clean_glob}')
    ),
    bad_threshold AS (
        SELECT DISTINCT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT FROM read_parquet('{threshold_glob}')
    ),
    bad_hard AS (
        SELECT DISTINCT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT FROM read_parquet('{hard_glob}')
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
    health AS (
        SELECT DT_CODE_NEW,
               100.0 * (present_rows - bad_rows) / present_rows AS health_pct
        FROM per_dt
    )
    SELECT
        CASE
            WHEN health_pct >= 95 THEN '95-100%'
            WHEN health_pct >= 90 THEN '90-95%'
            WHEN health_pct >= 75 THEN '75-90%'
            WHEN health_pct >= 50 THEN '50-75%'
            WHEN health_pct >= 25 THEN '25-50%'
            ELSE '0-25%'
        END AS bucket,
        COUNT(*) AS n_dts
    FROM health
    GROUP BY bucket
    ORDER BY bucket DESC
""").fetchdf()
print("\nHealth % distribution across all DTs:")
print(dist.to_string(index=False))

# 3. Does low health correlate with load GROWTH (rolling-band artifact)
#    rather than genuine faults? Compare avg peak-KVA trend for the bottom
#    quartile of health vs the top quartile.
growth_check = con.execute(f"""
    WITH all_readings AS (
        SELECT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT FROM read_parquet('{clean_glob}')
    ),
    bad_threshold AS (
        SELECT DISTINCT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT FROM read_parquet('{threshold_glob}')
    ),
    bad_hard AS (
        SELECT DISTINCT DT_CODE_NEW, MTR_NO, DATE, TIME_SLOT FROM read_parquet('{hard_glob}')
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
    health AS (
        SELECT DT_CODE_NEW, 100.0 * (present_rows - bad_rows) / present_rows AS health_pct
        FROM per_dt
    ),
    kva_span AS (
        SELECT DT_CODE_NEW,
               MIN(DATE) AS first_date, MAX(DATE) AS last_date,
               COUNT(DISTINCT DATE) AS n_days
        FROM read_parquet('{clean_glob}')
        GROUP BY DT_CODE_NEW
    )
    SELECT
        CASE WHEN h.health_pct < 75 THEN 'BELOW 75% (flagged)' ELSE 'AT/ABOVE 75% (healthy)' END AS group_label,
        COUNT(*) AS n_dts,
        AVG(k.n_days) AS avg_days_of_data,
        AVG(DATE_DIFF('day', k.first_date, k.last_date)) AS avg_span_days
    FROM health h
    JOIN kva_span k USING (DT_CODE_NEW)
    GROUP BY group_label
""").fetchdf()
print("\nData span comparison (below vs above 75% health) -- newer/shorter-history DTs")
print("would show up here if the issue is concentrated in recently-onboarded DTs:")
print(growth_check.to_string(index=False))