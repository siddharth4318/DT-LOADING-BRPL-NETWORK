import duckdb

con = duckdb.connect()

raw_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet/SOURCE_YM=202106/*.parquet"
clean_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/clean_data/month_202106.parquet"

print("=== Raw data columns ===")
raw_cols = con.execute(f'DESCRIBE SELECT * FROM read_parquet("{raw_path}") LIMIT 1').df()
print(raw_cols)

print("\n=== Clean data columns ===")
clean_cols = con.execute(f'DESCRIBE SELECT * FROM read_parquet("{clean_path}") LIMIT 1').df()
print(clean_cols)

print("\n=== Key differences ===")
raw_col_set = set(raw_cols['column_name'])
clean_col_set = set(clean_cols['column_name'])

print(f"Columns only in raw: {raw_col_set - clean_col_set}")
print(f"Columns only in clean: {clean_col_set - raw_col_set}")
