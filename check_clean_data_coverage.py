import duckdb
import pandas as pd

con = duckdb.connect()

# Check how many unique meters are in clean data for each month
print("=== Checking clean data coverage by month ===")
months = [202101, 202102, 202103, 202401, 202501, 202601, 202607]

for ym in months:
    file_path = f"c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_{ym}.parquet"
    try:
        meters = con.execute(f'''
            SELECT COUNT(DISTINCT MTR_NO) 
            FROM read_parquet("{file_path}")
        ''').fetchone()[0]
        print(f"Month {ym}: {meters:,} unique meters")
    except:
        print(f"Month {ym}: File not found")

# Check how many unique meters are in raw cache
print("\n=== Checking raw cache meter count ===")
raw_meters = con.execute('''
    SELECT COUNT(DISTINCT MTR_NO) 
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/monthly_peak_kva_raw.parquet")
''').fetchone()[0]
print(f"Raw cache: {raw_meters:,} unique meters")

# Check which meters have clean data for month 202607
print("\n=== Checking which meters have clean data for 202607 ===")
clean_meters_202607 = con.execute('''
    SELECT DISTINCT MTR_NO 
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
''').df()
print(f"Clean data for 202607: {len(clean_meters_202607):,} unique meters")
