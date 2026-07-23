import duckdb

con = duckdb.connect()

# Check if raw cache has MTR_NO column
raw_cache = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/monthly_peak_kva_raw.parquet"
clean_cache = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/monthly_peak_kva.parquet"

print("=== Raw Cache ===")
raw_df = con.execute(f'SELECT * FROM read_parquet("{raw_cache}") LIMIT 5').df()
print(f"Raw cache columns: {list(raw_df.columns)}")
print(f"Raw cache shape: {con.execute(f'SELECT COUNT(*) FROM read_parquet("{raw_cache}")').fetchone()[0]:,} rows")

print("\n=== Clean Cache ===")
clean_df = con.execute(f'SELECT * FROM read_parquet("{clean_cache}") LIMIT 5').df()
print(f"Clean cache columns: {list(clean_df.columns)}")
print(f"Clean cache shape: {con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_cache}")').fetchone()[0]:,} rows")

# Check MTR_NO overlap
print("\n=== MTR_NO Overlap ===")
raw_mtrs = con.execute(f'SELECT DISTINCT MTR_NO FROM read_parquet("{raw_cache}")').df()
clean_mtrs = con.execute(f'SELECT DISTINCT MTR_NO FROM read_parquet("{clean_cache}")').df()
print(f"Raw cache unique MTR_NO: {len(raw_mtrs):,}")
print(f"Clean cache unique MTR_NO: {len(clean_mtrs):,}")

# Check overlap
raw_mtrs_set = set(raw_mtrs['MTR_NO'].astype(str))
clean_mtrs_set = set(clean_mtrs['MTR_NO'].astype(str))
overlap = raw_mtrs_set & clean_mtrs_set
print(f"Overlapping MTR_NO: {len(overlap):,}")

# Check if raw cache has DT_CODE_NEW
print("\n=== Raw Cache DT_CODE_NEW ===")
if "DT_CODE_NEW" in raw_df.columns:
    print("Raw cache HAS DT_CODE_NEW column")
else:
    print("Raw cache DOES NOT have DT_CODE_NEW column")
