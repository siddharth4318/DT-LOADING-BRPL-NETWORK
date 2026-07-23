# DT Health Analytics & Load Forecasting Dashboard

A modular data engineering and analytics platform for Distribution Transformer (DT) health monitoring, anomaly detection, load analytics, and forecasting.

The project processes large-scale smart meter data, performs data validation and cleaning, detects operational anomalies, and provides interactive visualizations through a Streamlit dashboard.

---

## Features

- Data ingestion and preprocessing pipeline
- Smart meter data validation
- DT health diagnostics
- Anomaly detection
- Load curve visualization
- Peak loading analysis
- Sustained loading identification
- CAGR-based load growth analysis
- Load forecasting framework
- Interactive Streamlit dashboard

---

## Project Architecture

```
Raw Smart Meter Data
        │
        ▼
Data Cleaning & Validation
        │
        ▼
Feature Engineering
        │
        ▼
Anomaly Detection
        │
        ▼
DT Health Analytics
        │
        ▼
Forecasting Engine
        │
        ▼
Interactive Streamlit Dashboard
```

---

## Dashboard Modules

### 1. Feeder Analytics
- Distribution transformer overview
- Feeder-level statistics
- Load summaries

### 2. Load Curve Analysis
- Daily load profile visualization
- Historical comparison
- Peak demand trends

### 3. Peak kVA Analysis
- Peak demand identification
- Transformer utilization
- Capacity assessment

### 4. CAGR Analysis
- Historical growth analysis
- Long-term demand trends
- Feeder-wise comparison

### 5. Sustained Loading
- Continuous overload identification
- Loading duration analysis
- Capacity planning support

### 6. Forecasting
- Time-series load forecasting
- Demand prediction framework
- Weather-based forecasting support (under development)

### 7. Anomaly Detection
- Missing data detection
- Sudden load variation
- Voltage and current inconsistencies
- Operational anomaly reporting

---

## Repository Structure

```
dashboard/
│
├── app.py
├── fl_config.py
├── fl_data_helpers.py
├── fl_forecast_engine.py
├── fl_interp_engine.py
├── fl_kva_engine.py
├── fl_sustained_engine.py
├── tab1_fl_analytics.py
├── tab2_load_curve.py
├── tab3_peak_kva.py
├── tab4_cagr.py
├── tab5_sustained_loading.py
├── tab6_forecast.py
├── tab7_anomalies.py
└── requirements.txt

dt_pipeline.py
dt_health_diagnostic.py
dt_health_report.py

check_*.py
verify_clean_data.py
```

---

## Technologies Used

- Python
- Streamlit
- DuckDB
- Pandas
- NumPy
- Plotly
- Scikit-learn

---

## Installation

Clone the repository

```bash
git clone https://github.com/<your-username>/BRPL-DTS.git
```

Move into the project directory

```bash
cd BRPL-DTS
```

Install dependencies

```bash
pip install -r dashboard/requirements.txt
```

Run the dashboard

```bash
streamlit run dashboard/app.py
```

---

## Project Highlights

- Modular architecture
- Scalable data processing pipeline
- Interactive visual analytics
- Automated anomaly detection
- Time-series forecasting framework
- Large-scale smart meter data analytics

---

## Future Enhancements

- Weather-integrated XGBoost forecasting
- Transformer health scoring
- Predictive maintenance models
- Explainable AI for anomaly detection
- Automated report generation
- Cloud deployment
