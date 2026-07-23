import duckdb
import pandas as pd

con = duckdb.connect()

# Check if hard anomalies are matching clean data rows
print("=== Checking hard anomalies for month 202607 ===")
anomalies = con.execute('''
    SELECT * FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/hard_anomalies/month_202607.parquet")
    LIMIT 10
''').df()
print("Sample hard anomalies:")
print(anomalies)

# Count how many anomaly rows exist
anomaly_count = con.execute('''
    SELECT COUNT(*) FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/hard_anomalies/month_202607.parquet")
''').fetchone()[0]
print(f"\nTotal hard anomalies for month 202607: {anomaly_count:,}")

# Check if anomalies are matching clean data
print("\n=== Testing if anomalies match clean data ===")
match_test = con.execute('''
    SELECT COUNT(*) 
    FROM read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202607.parquet") c
    INNER JOIN read_parquet("c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/hard_anomalies/month_202607.parquet") a
        ON c.MTR_NO = a.MTR_NO AND c.DATE = a.DATE AND c.TIME_SLOT = a.TIME_SLOT
''').fetchone()[0]
print(f"Clean data rows matching hard anomalies: {match_test:,}")
