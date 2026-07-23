import duckdb

con = duckdb.connect()

cache_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/monthly_peak_kva.parquet"

print("=== Checking Raw vs Clean KVA Values ===")
df = con.execute(f'SELECT * FROM read_parquet("{cache_path}") LIMIT 20').df()
print("\nSample data:")
print(df[["MTR_NO", "YM", "PEAK_KVA_RAW", "PEAK_KVA_CLEAN"]])

# Check if clean values are different from raw
diff_count = con.execute(f'''
    SELECT COUNT(*) FROM read_parquet("{cache_path}") 
    WHERE PEAK_KVA_CLEAN IS NOT NULL 
    AND PEAK_KVA_CLEAN != PEAK_KVA_RAW
''').fetchone()[0]

same_count = con.execute(f'''
    SELECT COUNT(*) FROM read_parquet("{cache_path}") 
    WHERE PEAK_KVA_CLEAN IS NOT NULL 
    AND PEAK_KVA_CLEAN = PEAK_KVA_RAW
''').fetchone()[0]

total_clean = con.execute(f'''
    SELECT COUNT(*) FROM read_parquet("{cache_path}") 
    WHERE PEAK_KVA_CLEAN IS NOT NULL
''').fetchone()[0]

print(f"\nTotal non-null clean values: {total_clean:,}")
print(f"Clean values different from raw: {diff_count:,}")
print(f"Clean values same as raw: {same_count:,}")

if same_count > 0:
    print(f"\nWARNING: {same_count:,} clean values are identical to raw values")
    print("This suggests the clean data processing might not be working correctly.")
    
    # Show some examples where they are the same
    same_examples = con.execute(f'''
        SELECT MTR_NO, YM, PEAK_KVA_RAW, PEAK_KVA_CLEAN 
        FROM read_parquet("{cache_path}") 
        WHERE PEAK_KVA_CLEAN IS NOT NULL 
        AND PEAK_KVA_CLEAN = PEAK_KVA_RAW
        LIMIT 10
    ''').df()
    print("\nExamples where clean = raw:")
    print(same_examples)
