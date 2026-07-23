import os
import duckdb

# Check if clean cache exists
cache_dir = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache"
clean_cache = os.path.join(cache_dir, "monthly_peak_kva.parquet")
raw_cache = os.path.join(cache_dir, "monthly_peak_kva_raw.parquet")

print("=== Cache File Status ===")
print(f"Clean cache exists: {os.path.exists(clean_cache)}")
print(f"Raw cache exists: {os.path.exists(raw_cache)}")

if os.path.exists(clean_cache):
    con = duckdb.connect()
    df = con.execute(f'SELECT * FROM read_parquet("{clean_cache}") LIMIT 5').df()
    print(f"\nClean cache columns: {list(df.columns)}")
    print(f"Clean cache shape: {con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_cache}")').fetchone()[0]:,} rows")
    
    if "PEAK_KVA_CLEAN" in df.columns:
        clean_count = con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_cache}") WHERE PEAK_KVA_CLEAN IS NOT NULL').fetchone()[0]
        print(f"Non-null PEAK_KVA_CLEAN count: {clean_count:,}")
        
        # Sample some clean values
        sample = con.execute(f'SELECT MTR_NO, YM, PEAK_KVA_CLEAN FROM read_parquet("{clean_cache}") WHERE PEAK_KVA_CLEAN IS NOT NULL LIMIT 10').df()
        print("\nSample clean KVA values:")
        print(sample)
    else:
        print("PEAK_KVA_CLEAN column is MISSING from clean cache!")
