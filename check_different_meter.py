import duckdb
import os

con = duckdb.connect()

cache_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/monthly_peak_kva.parquet"

# Find a meter where clean != raw
diff_meter = con.execute(f'''
    SELECT MTR_NO, YM, PEAK_KVA_RAW, PEAK_KVA_CLEAN
    FROM read_parquet("{cache_path}")
    WHERE PEAK_KVA_CLEAN IS NOT NULL
    AND PEAK_KVA_CLEAN != PEAK_KVA_RAW
    LIMIT 1
''').fetchone()

print(f"Meter with different values: {diff_meter[0]}, YM: {diff_meter[1]}")
print(f"Raw: {diff_meter[2]}, Clean: {diff_meter[3]}")
print(f"Difference: {diff_meter[3] - diff_meter[2]}")

meter = diff_meter[0]
ym = diff_meter[1]

raw_path = f"c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet/SOURCE_YM={ym}/*.parquet"
clean_path = f"c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_{ym}.parquet"

granularity_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/meter_granularity.parquet"
gran = con.execute(f'''
    SELECT * FROM read_parquet("{granularity_path}")
    WHERE MTR_NO = '{meter}'
''').df()
print(f"\nGranularity: {gran['SLOT_MINUTES'].values[0]} min")

# Check peak slot in clean data
print("\n=== Peak slot in clean data ===")
clean_peak = con.execute(f'''
    SELECT *
    FROM read_parquet("{clean_path}")
    WHERE MTR_NO = '{meter}'
    ORDER BY (VR * IR + VY * IY + VB * IB) DESC
    LIMIT 3
''').df()
print(clean_peak[['MTR_NO', 'DATE', 'TIME_SLOT', 'VR', 'VY', 'VB', 'IR', 'IY', 'IB', 'KW_R', 'KW_Y', 'KW_B', 'was_zero_voltage']])

# Check peak slot in raw data
print("\n=== Peak slot in raw data ===")
raw_peak = con.execute(f'''
    SELECT *
    FROM read_parquet("{raw_path}")
    WHERE MTR_NO = '{meter}'
    ORDER BY (VR * IR + VY * IY + VB * IB) DESC
    LIMIT 3
''').df()
print(raw_peak[['MTR_NO', 'OCCUR_D_RAW', 'TIME_SLOT', 'VR', 'VY', 'VB', 'IR', 'IY', 'IB', 'KW_R', 'KW_Y', 'KW_B']])

# Check hard anomalies
hard_path = f"c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/hard_anomalies/month_{ym}.parquet"
if os.path.exists(hard_path):
    hard = con.execute(f'''
        SELECT * FROM read_parquet("{hard_path}")
        WHERE MTR_NO = '{meter}'
        LIMIT 5
    ''').df()
    print("\n=== Hard anomalies ===")
    print(hard)
