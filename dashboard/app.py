"""
app.py -- BRPL DTS Dashboard main entrypoint.

Run with: streamlit run app.py

PERFORMANCE NOTE: st.tabs() is purely a visual/CSS construct -- every
`with tabs[i]:` block still executes on EVERY script rerun, regardless of
which tab is visually selected. That meant every widget interaction on
any single tab was also re-running all 6 other tabs' queries and Plotly
figure builds in the background. This version uses st.radio() as the nav
control instead, and only imports + calls render() for the tab that is
actually selected -- so, e.g., being on "Load Curve" never touches Peak
KVA/CAGR/Sustained Loading/Forecast/Anomalies at all. This is on top of
(not a replacement for) the existing @st.cache_data layer in
fl_data_helpers.py / fl_kva_engine.py / fl_sustained_engine.py.
"""

import streamlit as st

st.set_page_config(page_title="BRPL DTS Dashboard", layout="wide")
st.title("BRPL DTS Load Survey Dashboard")

TAB_NAMES = [
    "FL Analytics",
    "Load Curve",
    "Peak KVA",
    "CAGR",
    "Sustained Loading",
    "Forecast",
    "Anomalies",
]

if "active_tab" not in st.session_state:
    st.session_state.active_tab = TAB_NAMES[0]

st.session_state.active_tab = st.radio(
    "Navigation",
    TAB_NAMES,
    horizontal=True,
    index=TAB_NAMES.index(st.session_state.active_tab),
    label_visibility="collapsed",
)

st.divider()

active = st.session_state.active_tab

# Lazy import: only the selected tab's module (and its heavy deps, e.g.
# xgboost/sklearn pulled in by tab2_load_curve via fl_optional_deps) gets
# imported at all -- not just skipped at render time.
if active == "FL Analytics":
    import tab1_fl_analytics as mod
elif active == "Load Curve":
    import tab2_load_curve as mod
elif active == "Peak KVA":
    import tab3_peak_kva as mod
elif active == "CAGR":
    import tab4_cagr as mod
elif active == "Sustained Loading":
    import tab5_sustained_loading as mod
elif active == "Forecast":
    import tab6_forecast as mod
elif active == "Anomalies":
    import tab7_anomalies as mod

mod.render()
