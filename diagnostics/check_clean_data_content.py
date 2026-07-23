import duckdb
import pandas as pd

con = duckdb.connect()

# Check what's actually in clean data files
print("=== Checking clean data content ===")
df = con.execute('''
    SELECT MTR_NO, DT_CODE_NEW, DATE, TIME_SLOT, VR, VY, VB, IR, IY, IB, KW_R, KW_Y, KW_B, KVAR_R, KVAR_Y, KVAR_B, KWH_TOTAL
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
    LIMIT 10
''').df()
print("Sample clean data:")
print(df)

# Check if clean data has KVA values already computed
print("\n=== Checking if clean data has KVA columns ===")
df2 = con.execute('''
    SELECT * FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
    LIMIT 1
''').df()
print("All columns in clean data:", df2.columns.tolist())

# Check the KVA calculation
print("\n=== Testing KVA calculation on clean data ===")
kva_test = con.execute('''
    SELECT 
        MTR_NO,
        DATE,
        TIME_SLOT,
        (VR * IR + VY * IY + VB * IB) / 1000.0 as KVA_CALCULATED,
        KW_R + KW_Y + KW_B as TOTAL_KW,
        KVAR_R + KVAR_Y + KVAR_B as TOTAL_KVAR,
        SQRT(POWER(KW_R + KW_Y + KW_B, 2) + POWER(KVAR_R + KVAR_Y + KVAR_B, 2)) as KVA_FROM_KW_KVAR
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
    LIMIT 5
''').df()
print(kva_test)
