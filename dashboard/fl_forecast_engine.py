"""
fl_forecast_engine.py -- "Best regression method" forecasting.

For each DT (or FL rollup), fits several candidate models against its
yearly peak KVA history and picks whichever has the lowest leave-one-out
cross-validation error. With only a handful of yearly points per DT, a
simple LOO error is more honest than a train/test split.

Candidates:
  - Linear:        KVA = a + b*year
  - Quadratic:     KVA = a + b*year + c*year^2
  - Exponential:   KVA = a * (1+r)^year   (equivalent to a CAGR projection --
                    fit via log-linear regression on ln(KVA))

Requires at least 3 yearly points to fit; returns None if not enough data.
Target year is passed in by the caller (tab6 defaults it to latest_year+1
dynamically -- nothing here is hardcoded to any specific year).
"""

import numpy as np


def _fit_linear(years, values):
    coeffs = np.polyfit(years, values, 1)
    return coeffs, lambda x: np.polyval(coeffs, x)


def _fit_quadratic(years, values):
    coeffs = np.polyfit(years, values, 2)
    return coeffs, lambda x: np.polyval(coeffs, x)


def _fit_exponential(years, values):
    values = np.clip(values, 1e-6, None)  # log requires positive values
    log_vals = np.log(values)
    coeffs = np.polyfit(years, log_vals, 1)  # ln(y) = b*year + ln(a)
    b, ln_a = coeffs
    return (ln_a, b), lambda x: np.exp(ln_a + b * np.asarray(x))


CANDIDATES = {
    "linear": _fit_linear,
    "quadratic": _fit_quadratic,
    "exponential": _fit_exponential,
}


def _loo_error(years, values, fit_fn):
    """Leave-one-out squared error, skipped gracefully if too few points remain."""
    years = np.asarray(years, dtype=float)
    values = np.asarray(values, dtype=float)
    n = len(years)
    if n < 4:
        return np.inf  # not enough points to hold one out and still fit meaningfully
    errors = []
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        try:
            _, predict = fit_fn(years[mask], values[mask])
            pred = predict(years[i])
            errors.append((pred - values[i]) ** 2)
        except Exception:
            errors.append(np.inf)
    return float(np.mean(errors))


def fit_best_model(years, values, target_year):
    """
    Returns dict with: method, predicted_value, fitted_curve (years, values),
    or None if there isn't enough history (need >= 3 yearly points).
    """
    years = np.asarray(years, dtype=float)
    values = np.asarray(values, dtype=float)
    valid = ~np.isnan(values)
    years, values = years[valid], values[valid]
    if len(years) < 3:
        return None

    best_method, best_err, best_fn = None, np.inf, None
    for name, fit_fn in CANDIDATES.items():
        err = _loo_error(years, values, fit_fn)
        if err < best_err:
            best_method, best_err, best_fn = name, err, fit_fn

    if best_fn is None:
        return None

    _, predict = best_fn(years, values)
    predicted_value = float(predict(target_year))
    plot_years = np.concatenate([years, [target_year]])
    plot_values = predict(plot_years)

    return {
        "method": best_method,
        "predicted_value": predicted_value,
        "loo_error": best_err,
        "plot_years": plot_years.tolist(),
        "plot_values": plot_values.tolist(),
        "history_years": years.tolist(),
        "history_values": values.tolist(),
    }
