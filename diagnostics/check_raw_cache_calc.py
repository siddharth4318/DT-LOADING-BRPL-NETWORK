import duckdb

con = duckdb.connect()

# Simulate the raw KVA calculation to see what's happening
raw_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/raw_parquet/SOURCE_YM=202106/*.parquet"
hierarchy_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/hierarchy.parquet"
granularity_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/meter_granularity.parquet"

meter = "29500001"

# Register tables
con.register("hierarchy", con.execute(f'SELECT * FROM read_parquet("{hierarchy_path}")').df())
con.register("granularity", con.execute(f'SELECT * FROM read_parquet("{granularity_path}")').df())

print("=== Simulating raw KVA calculation ===")

# Check hierarchy join
print("\n=== Hierarchy join result ===")
join_result = con.execute(f'''
    SELECT c.MTR_NO, h.CT_RATIO, h.DT_CODE_NEW
    FROM read_parquet("{raw_path}") c
    LEFT JOIN hierarchy h ON c.MTR_NO = h.MTR_NO
    WHERE c.MTR_NO = '{meter}'
    LIMIT 1
''').df()
print(join_result)

# Check granularity join
print("\n=== Granularity join result ===")
gran_result = con.execute(f'''
    SELECT c.MTR_NO, g.SLOT_MINUTES
    FROM read_parquet("{raw_path}") c
    LEFT JOIN granularity g ON c.MTR_NO = g.MTR_NO
    WHERE c.MTR_NO = '{meter}'
    LIMIT 1
''').df()
print(gran_result)

# Simulate the actual KVA calculation
print("\n=== Simulated KVA calculation ===")
simulated = con.execute(f'''
    SELECT
        c.MTR_NO,
        h.CT_RATIO,
        g.SLOT_MINUTES,
        MAX(CASE WHEN g.SLOT_MINUTES = 15
                 THEN ((c.VR * c.IR + c.VY * c.IY + c.VB * c.IB) / 1000.0) * COALESCE(h.CT_RATIO, 1)
                 ELSE SQRT(POWER(c.KW_R + c.KW_Y + c.KW_B, 2)
                         + POWER(c.KVAR_R + c.KVAR_Y + c.KVAR_B, 2)) * COALESCE(h.CT_RATIO, 1)
            END) AS PEAK_KVA_RAW
    FROM read_parquet("{raw_path}") c
    LEFT JOIN hierarchy h ON c.MTR_NO = h.MTR_NO
    LEFT JOIN granularity g ON c.MTR_NO = g.MTR_NO
    WHERE c.MTR_NO = '{meter}' AND h.DT_CODE_NEW IS NOT NULL
    GROUP BY c.MTR_NO, h.CT_RATIO, g.SLOT_MINUTES
''').df()
print(simulated)

# Compare with cache
cache_path = "c:/Users/Siddharth Jain/Documents/BRPL DTS/pipeline_output/dashboard_cache/monthly_peak_kva.parquet"
cache = con.execute(f'''
    SELECT MTR_NO, YM, PEAK_KVA_RAW, PEAK_KVA_CLEAN
    FROM read_parquet("{cache_path}")
    WHERE MTR_NO = '{meter}' AND YM = 202106
''').df()
print("\n=== Cache value ===")
print(cache)
