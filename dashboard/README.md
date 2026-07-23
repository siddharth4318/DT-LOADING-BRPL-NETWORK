# BRPL DTS Dashboard — Setup & Run

## 1. Install

Copy all 15 files into a fresh folder (e.g. `C:\Users\Siddharth Jain\Documents\BRPL DTS\dashboard\`),
then from that folder:

```powershell
pip install -r requirements.txt --break-system-packages
```

## 2. Run

```powershell
streamlit run app.py
```

It'll open in your browser automatically (usually http://localhost:8501).

## 3. First load will be slower — that's expected

Per your own instruction (pre-aggregate once at startup, not live-query on every
click), the first time you open the dashboard it will:
1. Detect each meter's slot granularity (15-min vs 30-min) — scans clean_data once.
2. Build the FL→DT→Meter hierarchy — reads master_cache.parquet + meter_seq_cache.parquet.
3. Build the monthly peak-KVA cache (raw + clean) — scans clean_data + anomaly
   files once, and for the *clean* series, runs gap-length-aware interpolation
   over every meter-month. **This is the slow step at 10,000-DT scale** — could
   take several minutes on the very first run, depending on how many months
   your pipeline has processed.
4. Build the sustained-loading cache.

All four are cached (`st.cache_data` / `st.cache_resource`). Every filter change
after that first load is instant — it's just reading the already-built tables.
The cache only rebuilds automatically if `dt_pipeline.py` produces a *new* month
of `clean_data`.

If step 3 is too slow in practice at full 10,000-DT scale, the fix is to move
that computation out of the live Streamlit process into a separate offline
script that writes `pipeline_output/dashboard_cache/monthly_peak_kva.parquet`
on a schedule (e.g. right after `dt_pipeline.py` finishes) — then this dashboard
just reads that file directly instead of computing it live. Say the word if you
want that follow-up script; it's a straightforward extraction of the same logic
in `fl_kva_engine.py`.

## 4. If something doesn't render / errors on startup

Two schema assumptions I had to make without seeing `dt_pipeline.py`'s actual
output, both isolated to `fl_data_helpers.py`:

- **Month detection** (`list_available_months()`): assumes `clean_data/` has a
  6-digit `YYYYMM` somewhere in each file's path/name. If your pipeline names
  files differently (e.g. one big file, or partitioned by year only), tell me
  the actual layout and I'll fix that one function.
- **Hierarchy join** (`_build_hierarchy()`): assumes `master_cache.parquet` has
  `MTR_NO`/`DT_METER_NO`, `DT_CODE_NEW`, `SDO_CD` columns (matching `fl_config.py`),
  and that `meter_seq_cache.parquet` has a `meter_seq` column keyed by meter+DT.
  If a column is missing, the app will show a clear `st.error()` with the actual
  columns found — paste that message back to me and I'll align it exactly.

Every other file only depends on `fl_config.py` column names and the pipeline's
own Parquet outputs (`clean_data`, `hard_anomalies`, `threshold_anomalies`,
`missing_slot_days_dt`) — no other guessing was needed there.

## 5. What changed vs. your reference bundle

- **`fl_data_helpers.py`** — written from scratch (this was the actual missing
  piece; the file with this name in your upload was a different, old,
  single-file dashboard with the bugs you were warning me about — hardcoded
  year, a CAGR bootstrap that shuffles years and breaks chronology, etc. — not
  a helper module, so nothing from it was reused).
- **`fl_kva_engine.py`** — rewritten so "clean" KVA is a genuine reconstruction
  (interpolate missing + anomaly-flagged slots, then take the peak of the
  rebuilt curve), not just excluding bad points from the max like the old
  version did.
- Everything else (`fl_config.py`, `fl_interp_engine.py`, `fl_forecast_engine.py`,
  `fl_sustained_engine.py`, `fl_optional_deps.py`, `app.py`, all 7 `tab*.py`
  files) — kept from your reference bundle, verified against every spec point
  in your message, unchanged.

All 15 files were compiled and cross-checked (every `from X import Y` between
modules resolves to something that actually exists) before delivery.
