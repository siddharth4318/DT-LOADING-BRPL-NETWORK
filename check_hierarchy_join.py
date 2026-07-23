import duckdb

con = duckdb.connect()

hierarchy_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/hierarchy.parquet"

print("=== Check if hierarchy has CT_RATIO column ===")
hierarchy_cols = con.execute(f'DESCRIBE SELECT * FROM read_parquet("{hierarchy_path}") LIMIT 1').df()
print(hierarchy_cols)

print("\n=== Sample hierarchy data ===")
hierarchy_sample = con.execute(f'''
    SELECT MTR_NO, DT_CODE_NEW, CT_RATIO, KVA_RATING
    FROM read_parquet("{hierarchy_path}")
    LIMIT 5
''').df()
print(hierarchy_sample)

print("\n=== Check for meter 29500001 specifically ===")
meter_sample = con.execute(f'''
    SELECT MTR_NO, DT_CODE_NEW, CT_RATIO, KVA_RATING
    FROM read_parquet("{hierarchy_path}")
    WHERE MTR_NO = '29500001'
''').df()
print(meter_sample)
