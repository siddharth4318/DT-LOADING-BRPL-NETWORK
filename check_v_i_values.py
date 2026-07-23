import duckdb
import pandas as pd

con = duckdb.connect()

# Check if VR, VY, VB, IR, IY, IB have actual values in clean data
print("=== Checking VR, VY, VB, IR, IY, IB values in clean data ===")
df = con.execute('''
    SELECT 
        MTR_NO,
        DATE,
        TIME_SLOT,
        VR, VY, VB,
        IR, IY, IB,
        (VR * IR + VY * IY + VB * IB) / 1000.0 as KVA_CALCULATED
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
    WHERE VR IS NOT NULL AND IR IS NOT NULL
    LIMIT 10
''').df()
print("Sample with non-null VR and IR:")
print(df)

# Count how many rows have non-null VR and IR
count = con.execute('''
    SELECT COUNT(*) 
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
    WHERE VR IS NOT NULL AND IR IS NOT NULL
''').fetchone()[0]
print(f"\nTotal rows with non-null VR and IR: {count:,}")

# Check total rows in clean data
total = con.execute('''
    SELECT COUNT(*) 
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet")
''').fetchone()[0]
print(f"Total rows in clean data: {total:,}")
