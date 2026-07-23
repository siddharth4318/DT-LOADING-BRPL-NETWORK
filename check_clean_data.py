import duckdb
import pandas as pd

con = duckdb.connect()

# Check structure of a clean data file
print("=== Checking clean data file structure ===")
df = con.execute('SELECT * FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet") LIMIT 5').df()
print("Columns:", df.columns.tolist())
print("\nSample data:")
print(df.head())

# Check if clean cache exists
print("\n=== Checking clean cache ===")
import os
cache_dir = "c:/Users/Siddharth Jain/Documents/BRPL DTS/dashboard/cache"
clean_cache = os.path.join(cache_dir, "monthly_peak_kva.parquet")
if os.path.exists(clean_cache):
    print(f"Clean cache exists: {clean_cache}")
    cache_df = con.execute(f'SELECT * FROM read_parquet("{clean_cache}") LIMIT 5').df()
    print("Cache columns:", cache_df.columns.tolist())
    print("Cache shape:", con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_cache}")').fetchone()[0])
    if "PEAK_KVA_CLEAN" in cache_df.columns:
        clean_count = con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_cache}") WHERE PEAK_KVA_CLEAN IS NOT NULL').fetchone()[0]
        print(f"Non-null PEAK_KVA_CLEAN rows: {clean_count}")
else:
    print("Clean cache does NOT exist")

# Check raw cache
print("\n=== Checking raw cache ===")
raw_cache = os.path.join(cache_dir, "monthly_peak_kva_raw.parquet")
if os.path.exists(raw_cache):
    print(f"Raw cache exists: {raw_cache}")
    raw_df = con.execute(f'SELECT * FROM read_parquet("{raw_cache}") LIMIT 5').df()
    print("Raw cache columns:", raw_df.columns.tolist())
    print("Raw cache shape:", con.execute(f'SELECT COUNT(*) FROM read_parquet("{raw_cache}")').fetchone()[0])
else:
    print("Raw cache does NOT exist")
