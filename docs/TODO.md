# FIX PLAN — Comprehensive Issues Resolution

## ✅ Completed Fixes (Round 1 + Round 2 Critical Bugfixes)

| # | Issue | Status | Files Changed |
|---|-------|--------|---------------|
| 1 | Load Curve - missing slots detection | ✅ Fixed | `tab2_load_curve.py` |
| 2 | XGBoost diagnostics + error handling | ✅ Fixed | `fl_optional_deps.py`, `tab2_load_curve.py` |
| 3 | Clean KVA curve reconstruction (MAJOR) | ✅ Fixed | `fl_kva_engine.py` — complete rewrite |
| 4 | Peak KVA tab - clean display + session state | ✅ Fixed | `tab3_peak_kva.py` |
| 5 | Sustained Loading - per-month caching, concurrency, SQL fix | ✅ Fixed | `fl_sustained_engine.py` |
| 6 | Sustained Loading tab - import from engine | ✅ Fixed | `tab5_sustained_loading.py` |
| **7** | **RAW builder reading CLEAN cache (raw==clean bug)** | 🔴 **CRITICAL FIX** | `fl_kva_engine.py` — `_build_monthly_peak_kva` now reads `MONTHLY_PEAK_RAW_CACHE` not `MONTHLY_PEAK_CACHE` |
| **8** | **CachedStFunctionError crash** | 🔴 **CRITICAL FIX** | `fl_kva_engine.py`, `fl_sustained_engine.py` — removed `@st.cache_data` from functions using `st.progress()` |
| **9** | **Cache version invalidation** | 🔴 **CRITICAL FIX** | `fl_data_helpers.py` — added `CACHE_VERSION = "v2"` prefix to meta signature, old caches auto-rebuild |
| **10** | **Missing imports** | 🔴 **FIX** | `fl_sustained_engine.py` — added `hard_anomalies_glob`, `threshold_anomalies_glob` |

## 🔍 Root Cause — Why RAW == CLEAN

**Root Cause:**
In `fl_kva_engine.py`, the RAW builder `_build_monthly_peak_kva()` read from `MONTHLY_PEAK_CACHE` (the CLEAN cache file) as its fast-path. Since the CLEAN cache contains both PEAK_KVA_RAW and PEAK_KVA_CLEAN columns, calling `get_monthly_peak_kva_cached()` returned the CLEAN cache — which ALSO had the old wrong PEAK_KVA_CLEAN that was just `MAX(KVA)` from SQL (same as PEAK_KVA_RAW).

**Fix:**
- RAW builder now reads from `MONTHLY_PEAK_RAW_CACHE` (separate file)
- CLEAN builder reads from `MONTHLY_PEAK_CACHE` 
- Cache format versioned with `CACHE_VERSION = "v2"` so all old caches are invalidated

**Why the curve reconstruction code wasn't being invoked:**
Even though `_build_monthly_peak_kva_clean()` had the new correct logic (anomaly zeroing + interpolation), it was NEVER being called because:
1. `get_monthly_peak_kva_cached()` returned the OLD CLEAN cache (complete with wrong PEAK_KVA_CLEAN)
2. The tab would see PEAK_KVA_CLEAN exists and show it as-is
3. The BUILD button flow had caching issues from `@st.cache_data` decorators

## 🚨 Instructions for Testing

### Step 1: Clear ALL old caches
```
del /s /q "c:\Users\Siddharth Jain\Documents\BRPL DTS\pipeline_output\dashboard_cache\*.*" 2>nul
```

### Step 2: Start dashboard
```
streamlit run app.py
```

### Step 3: Test Sustained Loading
- Tab should open immediately without CachedStFunctionError
- If no cache exists: click "Build Sustained Loading Cache (RAW only)"
- Progress prints to console (Terminal window)
- After build, all filters/selections are instant

### Step 4: Test Peak KVA Clean
- Navigate to Peak KVA tab
- Check "Use Clean KVA" → click "Build Clean KVA Cache" button
- First build processes months one-by-one with curve reconstruction (console shows progress)
- After build: Clean KVA will show DIFFERENT values from Raw KVA (lower where anomalies existed)
- Future visits load instantly from disk cache

### Step 5: Verify RAW ≠ CLEAN
- Raw KVA = original MAX(KVA) from raw_data (no anomaly handling)
- Clean KVA = per-meter-day curve reconstruction: anomaly slots zeroed → interpolated → MAX of rebuilt curve

## 📝 Remaining Items (non-blocking)

- [ ] CAGR tab's DT-level view uses undefined `yearly_dt_raw` variable (error on DT view)
- [ ] Verify XGBoost in Load Curve tab actually trains models (not just falls back silently)
- [ ] Monitor clean KVA build time for large fleet (10,000 DTs) — may need batch optimization


