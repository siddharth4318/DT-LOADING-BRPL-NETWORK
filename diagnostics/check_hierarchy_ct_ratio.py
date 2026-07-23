import duckdb
import os

con = duckdb.connect()

master_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/master_cache.parquet"

print("=== Master cache columns ===")
master_cols = con.execute(f'DESCRIBE SELECT * FROM read_parquet("{master_path}") LIMIT 1').df()
print(master_cols)

print("\n=== Sample master data with CT_RATIO ===")
master_sample = con.execute(f'''
    SELECT MTR_NO, DT_CODE_NEW, CT_RATIO, KVA_RATING
    FROM read_parquet("{master_path}")
    LIMIT 10
''').df()
print(master_sample)

print("\n=== Check hierarchy cache ===")
hierarchy_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/hierarchy.parquet"
if os.path.exists(hierarchy_path):
    hierarchy_cols = con.execute(f'DESCRIBE SELECT * FROM read_parquet("{hierarchy_path}") LIMIT 1').df()
    print("Hierarchy columns:")
    print(hierarchy_cols)
    
    hierarchy_sample = con.execute(f'''
        SELECT * FROM read_parquet("{hierarchy_path}")
        LIMIT 5
    ''').df()
    print("\nHierarchy sample:")
    print(hierarchy_sample)
else:
    print("Hierarchy cache not found")
