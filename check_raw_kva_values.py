import duckdb

con = duckdb.connect()

raw_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet/SOURCE_YM=202106/*.parquet"
clean_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202106.parquet"

print("=== Raw data sample values ===")
raw_sample = con.execute(f'''
    SELECT MTR_NO, VR, VY, VB, IR, IY, IB, KW_R, KW_Y, KW_B, KVAR_R, KVAR_Y, KVAR_B
    FROM read_parquet("{raw_path}")
    LIMIT 5
''').df()
print(raw_sample)

print("\n=== Clean data sample values ===")
clean_sample = con.execute(f'''
    SELECT MTR_NO, VR, VY, VB, IR, IY, IB, KW_R, KW_Y, KW_B, KVAR_R, KVAR_Y, KVAR_B
    FROM read_parquet("{clean_path}")
    LIMIT 5
''').df()
print(clean_sample)

print("\n=== Calculate KVA for same meter/slot ===")
# Get a specific meter from raw
meter = "29500001"
raw_slot = con.execute(f'''
    SELECT MTR_NO, VR, VY, VB, IR, IY, IB, KW_R, KW_Y, KW_B, KVAR_R, KVAR_Y, KVAR_B
    FROM read_parquet("{raw_path}")
    WHERE MTR_NO = '{meter}'
    LIMIT 1
''').df()
print("Raw slot:")
print(raw_slot)

if not raw_slot.empty:
    row = raw_slot.iloc[0]
    kva_15min = (row['VR'] * row['IR'] + row['VY'] * row['IY'] + row['VB'] * row['IB']) / 1000.0
    kva_30min = ((row['KW_R'] + row['KW_Y'] + row['KW_B'])**2 + (row['KVAR_R'] + row['KVAR_Y'] + row['KVAR_B'])**2)**0.5
    print(f"\nKVA (15-min formula): {kva_15min}")
    print(f"KVA (30-min formula): {kva_30min}")

# Get same meter from clean
clean_slot = con.execute(f'''
    SELECT MTR_NO, VR, VY, VB, IR, IY, IB, KW_R, KW_Y, KW_B, KVAR_R, KVAR_Y, KVAR_B
    FROM read_parquet("{clean_path}")
    WHERE MTR_NO = '{meter}'
    LIMIT 1
''').df()
print("\nClean slot:")
print(clean_slot)

if not clean_slot.empty:
    row = clean_slot.iloc[0]
    kva_15min = (row['VR'] * row['IR'] + row['VY'] * row['IY'] + row['VB'] * row['IB']) / 1000.0
    kva_30min = ((row['KW_R'] + row['KW_Y'] + row['KW_B'])**2 + (row['KVAR_R'] + row['KVAR_Y'] + row['KVAR_B'])**2)**0.5
    print(f"\nKVA (15-min formula): {kva_15min}")
    print(f"KVA (30-min formula): {kva_30min}")
