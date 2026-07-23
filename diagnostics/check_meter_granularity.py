import duckdb

con = duckdb.connect()

granularity_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/meter_granularity.parquet"

meter = "29500001"

print(f"=== Checking granularity for meter {meter} ===")
gran = con.execute(f'''
    SELECT * FROM read_parquet("{granularity_path}")
    WHERE MTR_NO = '{meter}'
''').df()
print(gran)

print("\n=== Checking if this meter has any non-null IR values in clean data ===")
clean_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202106.parquet"
ir_count = con.execute(f'''
    SELECT COUNT(*) FROM read_parquet("{clean_path}")
    WHERE MTR_NO = '{meter}' AND IR IS NOT NULL
''').fetchone()[0]
print(f"Non-null IR count: {ir_count}")

print("\n=== Checking if this meter has any non-null IR values in raw data ===")
raw_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet/SOURCE_YM=202106/*.parquet"
ir_count_raw = con.execute(f'''
    SELECT COUNT(*) FROM read_parquet("{raw_path}")
    WHERE MTR_NO = '{meter}' AND IR IS NOT NULL
''').fetchone()[0]
print(f"Non-null IR count (raw): {ir_count_raw}")

print("\n=== Sample of KW/KVAR values in clean data ===")
kw_sample = con.execute(f'''
    SELECT MTR_NO, DATE, TIME_SLOT, KW_R, KW_Y, KW_B, KVAR_R, KVAR_Y, KVAR_B
    FROM read_parquet("{clean_path}")
    WHERE MTR_NO = '{meter}'
    LIMIT 5
''').df()
print(kw_sample)
