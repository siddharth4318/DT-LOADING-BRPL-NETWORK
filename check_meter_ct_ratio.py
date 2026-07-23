import duckdb

con = duckdb.connect()

hierarchy_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/hierarchy.parquet"
meter = "29500001"

print(f"=== Checking CT_RATIO for meter {meter} ===")
hierarchy = con.execute(f'''
    SELECT * FROM read_parquet("{hierarchy_path}")
    WHERE MTR_NO = '{meter}'
''').df()
print(hierarchy)

print("\n=== Checking master cache for this meter ===")
master_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/master_cache.parquet"
master = con.execute(f'''
    SELECT * FROM read_parquet("{master_path}")
    WHERE MTR_NO = '{meter}'
    LIMIT 5
''').df()
print(master[['MTR_NO', 'DT_CODE_NEW', 'CT_RATIO', 'KVA_RATING']])

print("\n=== Calculating expected raw KVA with CT_RATIO ===")
# From raw data peak slot: KW_R=0.3059, KW_Y=0.2829, KW_B=0.4209, KVAR_R=0.0575, KVAR_Y=0.0391, KVAR_B=0.0621
kw_total = 0.3059 + 0.2829 + 0.4209
kvar_total = 0.0575 + 0.0391 + 0.0621
kva_unscaled = (kw_total**2 + kvar_total**2)**0.5
print(f"KVA (unscaled): {kva_unscaled}")

# Get CT_RATIO from hierarchy
ct_ratio = hierarchy['CT_RATIO'].values[0]
print(f"CT_RATIO: {ct_ratio}")
print(f"KVA (with CT_RATIO): {kva_unscaled * ct_ratio}")

# From clean data peak slot: KW_R~100, KW_Y~100, KW_B~120
kw_total_clean = 100 + 100 + 120
kvar_total_clean = 27 + 25 + 28
kva_clean = (kw_total_clean**2 + kvar_total_clean**2)**0.5
print(f"KVA (clean, already scaled): {kva_clean}")
