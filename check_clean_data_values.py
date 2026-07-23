import duckdb

con = duckdb.connect()

# Check if clean data files actually have different values from raw data
raw_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet/SOURCE_YM=202106/*.parquet"
clean_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202106.parquet"

print("=== Checking if clean data files have different values ===")

# Check VR nulls
raw_vr_nulls = con.execute(f'SELECT COUNT(*) FROM read_parquet("{raw_path}") WHERE VR IS NULL').fetchone()[0]
clean_vr_nulls = con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_path}") WHERE VR IS NULL').fetchone()[0]
print(f"Raw VR nulls: {raw_vr_nulls:,}")
print(f"Clean VR nulls: {clean_vr_nulls:,}")

# Check if clean data has more nulls (indicating anomaly removal)
if clean_vr_nulls > raw_vr_nulls:
    print(f"Clean data has {clean_vr_nulls - raw_vr_nulls:,} more VR nulls - this is expected (anomaly removal)")
else:
    print("WARNING: Clean data does not have more nulls than raw data - anomaly removal may not be working")

# Check was_zero_voltage flag
zero_volt_count = con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_path}") WHERE was_zero_voltage = TRUE').fetchone()[0]
print(f"\nRows flagged as zero voltage in clean data: {zero_volt_count:,}")

# Check if VR is actually NULL for those zero voltage rows
zero_volt_null_vr = con.execute(f'SELECT COUNT(*) FROM read_parquet("{clean_path}") WHERE was_zero_voltage = TRUE AND VR IS NULL').fetchone()[0]
print(f"Zero voltage rows with VR NULL: {zero_volt_null_vr:,}")

if zero_volt_count > 0 and zero_volt_null_vr == 0:
    print("ERROR: Zero voltage flagged but VR not nulled - clean data processing not working correctly")
