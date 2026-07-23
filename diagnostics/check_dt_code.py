import duckdb
import pandas as pd

con = duckdb.connect()

# Check if DT_CODE_NEW is NULL in clean data
print("=== Checking DT_CODE_NEW in clean data ===")
null_dt = con.execute('''
    SELECT COUNT(*) 
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
    WHERE DT_CODE_NEW IS NULL
''').fetchone()[0]
print(f"Rows with NULL DT_CODE_NEW: {null_dt:,}")

non_null_dt = con.execute('''
    SELECT COUNT(*) 
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
    WHERE DT_CODE_NEW IS NOT NULL
''').fetchone()[0]
print(f"Rows with non-NULL DT_CODE_NEW: {non_null_dt:,}")

# Check if the clean cache building is actually processing data
print("\n=== Testing the clean cache SQL query directly ===")
test_query = con.execute('''
    SELECT
        c.MTR_NO,
        c.DT_CODE_NEW,
        CAST(strftime(c.DATE, '%Y') AS INTEGER) AS YEAR,
        202607 AS YM,
        MAX((VR * IR + VY * IY + VB * IB) / 1000.0) AS PEAK_KVA_CLEAN
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet") c
    WHERE c.DT_CODE_NEW IS NOT NULL
    GROUP BY c.MTR_NO, c.DT_CODE_NEW, YEAR
    LIMIT 5
''').df()
print("Sample result from clean cache query:")
print(test_query)

# Count how many meter-month combinations we should get
count = con.execute('''
    SELECT COUNT(DISTINCT (MTR_NO, DT_CODE_NEW, CAST(strftime(DATE, '%Y') AS INTEGER)))
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
    WHERE DT_CODE_NEW IS NOT NULL
''').fetchone()[0]
print(f"\nExpected meter-month combinations: {count:,}")
