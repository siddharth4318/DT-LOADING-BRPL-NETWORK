import duckdb
import os

con = duckdb.connect()

# Check which months have clean data
clean_dir = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data"
clean_months = [int(f.replace("month_", "").replace(".parquet", "")) 
                 for f in os.listdir(clean_dir) if f.endswith(".parquet")]

print(f"=== Clean Data Coverage ===")
print(f"Months with clean data: {len(clean_months)}")
print(f"Clean months: {sorted(clean_months)}")

# Check which months have raw data
raw_dir = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet"
if os.path.exists(raw_dir):
    raw_months = [int(f.replace("month_", "").replace(".parquet", "")) 
                  for f in os.listdir(raw_dir) if f.endswith(".parquet")]
    print(f"\nMonths with raw data: {len(raw_months)}")
    print(f"Raw months: {sorted(raw_months)}")
    
    missing_months = set(raw_months) - set(clean_months)
    if missing_months:
        print(f"\nWARNING: Months with raw data but NO clean data: {sorted(missing_months)}")
    else:
        print(f"\nOK: All raw months have clean data")

# Check total row counts for a sample month
sample_month = 202607
clean_file = f"c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_{sample_month}.parquet"
if os.path.exists(clean_file):
    clean_count = con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_file}")').fetchone()[0]
    clean_meters = con.execute(f'SELECT COUNT(DISTINCT MTR_NO) FROM read_parquet("{clean_file}")').fetchone()[0]
    print(f"\n=== Sample Month {sample_month} ===")
    print(f"Clean data rows: {clean_count:,}")
    print(f"Clean data unique meters: {clean_meters:,}")
    
    # Check if this matches what we expect from the pipeline
    print(f"\nThis suggests the pipeline is only processing a subset of meters/months")
    print(f"or the clean data generation has filtering that shouldn't be there.")
