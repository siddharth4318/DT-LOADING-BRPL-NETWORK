import duckdb
import os

con = duckdb.connect()

# Check a specific meter where clean = raw
meter = "29500001"
ym = "202106"

raw_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet/SOURCE_YM=202106/*.parquet"
clean_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202106.parquet"

print(f"=== Checking meter {meter} for month {ym} ===")

# Get peak KVA from cache
cache_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/monthly_peak_kva.parquet"
cache_peak = con.execute(f'''
    SELECT PEAK_KVA_RAW, PEAK_KVA_CLEAN
    FROM read_parquet("{cache_path}")
    WHERE MTR_NO = '{meter}' AND YM = {ym}
''').fetchone()
print(f"Cache peak KVA - Raw: {cache_peak[0]}, Clean: {cache_peak[1]}")

# Check if the peak slot in clean data has any anomaly flags
clean_peak_slot = con.execute(f'''
    SELECT *
    FROM read_parquet("{clean_path}")
    WHERE MTR_NO = '{meter}'
    ORDER BY (VR * IR + VY * IY + VB * IB) DESC
    LIMIT 5
''').df()
print("\nTop 5 slots by KVA in clean data:")
print(clean_peak_slot[['MTR_NO', 'DATE', 'TIME_SLOT', 'VR', 'VY', 'VB', 'IR', 'IY', 'IB', 'was_zero_voltage']])

# Check if those slots were flagged as anomalies in hard_anomalies
hard_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/hard_anomalies/month_202106.parquet"
if os.path.exists(hard_path):
    hard_anomalies = con.execute(f'''
        SELECT * FROM read_parquet("{hard_path}")
        WHERE MTR_NO = '{meter}'
        LIMIT 5
    ''').df()
    print("\nHard anomalies for this meter:")
    print(hard_anomalies)
else:
    print("\nNo hard anomalies file found")

# Check raw data peak
raw_peak_slot = con.execute(f'''
    SELECT *
    FROM read_parquet("{raw_path}")
    WHERE MTR_NO = '{meter}'
    ORDER BY (VR * IR + VY * IY + VB * IB) DESC
    LIMIT 5
''').df()
print("\nTop 5 slots by KVA in raw data:")
print(raw_peak_slot[['MTR_NO', 'OCCUR_D_RAW', 'TIME_SLOT', 'VR', 'VY', 'VB', 'IR', 'IY', 'IB']])
