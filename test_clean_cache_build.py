import sys
sys.path.insert(0, 'c:/Users/Siddharth Jain/Documents/BRPL DTS/dashboard')

from fl_kva_engine import get_monthly_peak_kva_cached, get_monthly_peak_kva_clean_cached
import fl_config as cfg

print("=== Testing cache building ===")
print(f"Clean data dir: {cfg.CLEAN_DATA_DIR}")
print(f"Dashboard cache dir: {cfg.DASH_CACHE_DIR}")

print("\n=== Building RAW cache first ===")
try:
    raw_cache = get_monthly_peak_kva_cached()
    if raw_cache is not None and not raw_cache.empty:
        print(f"RAW cache built successfully: {raw_cache.shape} rows")
        print(f"RAW cache columns: {list(raw_cache.columns)}")
    else:
        print("RAW cache is empty or None")
except Exception as e:
    print(f"Error building RAW cache: {e}")
    import traceback
    traceback.print_exc()

print("\n=== Building CLEAN cache ===")
try:
    clean_cache = get_monthly_peak_kva_clean_cached()
    if clean_cache is not None and not clean_cache.empty:
        print(f"CLEAN cache built successfully: {clean_cache.shape} rows")
        print(f"CLEAN cache columns: {list(clean_cache.columns)}")
        if "PEAK_KVA_CLEAN" in clean_cache.columns:
            print(f"PEAK_KVA_CLEAN non-null count: {clean_cache['PEAK_KVA_CLEAN'].notna().sum()}")
            print(f"PEAK_KVA_CLEAN sample: {clean_cache['PEAK_KVA_CLEAN'].head(10).tolist()}")
        else:
            print("PEAK_KVA_CLEAN column is MISSING!")
    else:
        print("CLEAN cache is empty or None")
except Exception as e:
    print(f"Error building CLEAN cache: {e}")
    import traceback
    traceback.print_exc()
