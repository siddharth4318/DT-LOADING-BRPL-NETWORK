import duckdb

con = duckdb.connect()

# Check raw data for month 202607
raw_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet/SOURCE_YM=202607/*.parquet"
clean_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet"

print("=== Raw Data for Month 202607 ===")
try:
    raw_count = con.execute(f'SELECT COUNT(*) FROM read_parquet("{raw_path}")').fetchone()[0]
    raw_meters = con.execute(f'SELECT COUNT(DISTINCT MTR_NO) FROM read_parquet("{raw_path}")').fetchone()[0]
    print(f"Raw data rows: {raw_count:,}")
    print(f"Raw data unique meters: {raw_meters:,}")
except Exception as e:
    print(f"Error reading raw data: {e}")

print("\n=== Clean Data for Month 202607 ===")
clean_count = con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_path}")').fetchone()[0]
clean_meters = con.execute(f'SELECT COUNT(DISTINCT MTR_NO) FROM read_parquet("{clean_path}")').fetchone()[0]
print(f"Clean data rows: {clean_count:,}")
print(f"Clean data unique meters: {clean_meters:,}")

print("\n=== Comparison ===")
if raw_meters > clean_meters:
    print(f"PROBLEM: Raw data has {raw_meters:,} meters but clean data only has {clean_meters:,} meters")
    print(f"Missing meters in clean data: {raw_meters - clean_meters:,}")
elif raw_meters == clean_meters:
    print(f"OK: Raw and clean data have the same number of meters")
else:
    print(f"Unexpected: Clean data has more meters than raw data")
