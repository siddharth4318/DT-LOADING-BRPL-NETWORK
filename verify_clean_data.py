import duckdb

con = duckdb.connect()
n = con.execute("""
    SELECT COUNT(*) FROM read_parquet(
        'C:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/*.parquet'
    )
""").fetchone()[0]
print("Total clean_data rows:", f"{n:,}")

n_months = con.execute("""
    SELECT COUNT(DISTINCT filename) FROM read_parquet(
        'C:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/*.parquet',
        filename=true
    )
""").fetchone()[0]
print("Distinct month files counted:", n_months, "(expect 67)")