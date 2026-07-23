"""
fl_interp_engine.py -- Gap-length-aware interpolation, used by the Load
Curve tab (per-meter, interactive) AND by fl_kva_engine's fleet-wide
clean-KVA cache (same rule, applied at scale).

Rule (per Sid):
  - 1-2 consecutive missing slots  -> linear interpolation
  - 3-4 consecutive missing slots  -> quadratic interpolation
  - 5+ consecutive missing slots   -> cubic interpolation

pandas' built-in .interpolate() applies ONE method to the whole series, so
this walks the series, finds each contiguous NaN run, and interpolates
each run individually with the method its own length calls for -- using
a local window of surrounding valid points as the fit basis for quadratic/
cubic (a single global cubic fit across the whole series would overreact
to distant points).
"""

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

import fl_config as cfg


def _find_nan_runs(is_nan):
    """Returns list of (start_idx, end_idx_inclusive) for each contiguous run of True."""
    runs = []
    start = None
    for i, val in enumerate(is_nan):
        if val and start is None:
            start = i
        elif not val and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(is_nan) - 1))
    return runs


def interpolate_series(series: pd.Series, window: int = 6):
    """
    series: pandas Series indexed by slot order (e.g. 0..N-1 for a day/month
            of slots), values may contain NaN for missing readings.
    window: how many valid points on each side of a gap to use as the local
            fit basis for quadratic/cubic (linear only ever needs the two
            immediate neighbors).

    Returns: (filled_series, flags_series) where flags_series labels each
    filled point with the method used ('linear'/'quadratic'/'cubic'), and
    NaN/None for points that were never missing or couldn't be filled
    (e.g. a gap at the very start/end of the series with no neighbor).
    """
    values = series.to_numpy(dtype=float)
    n = len(values)
    if n == 0:
        return pd.Series(dtype=float), pd.Series(dtype=object)

    is_nan = np.isnan(values)
    filled = values.copy()
    method_flags = np.array([None] * n, dtype=object)

    runs = _find_nan_runs(is_nan)
    for start, end in runs:
        gap_len = end - start + 1
        if gap_len <= cfg.INTERP_LINEAR_MAX_GAP:
            method = "linear"
        elif gap_len <= cfg.INTERP_QUADRATIC_MAX_GAP:
            method = "quadratic"
        else:
            method = "cubic"

        left_idx = start - 1
        right_idx = end + 1
        if left_idx < 0 or right_idx >= n:
            # Gap touches the boundary of the series -- can't interpolate
            # (no anchor on one side); leave as NaN rather than guess.
            continue

        left_bound = max(0, left_idx - window + 1)
        right_bound = min(n - 1, right_idx + window - 1)
        local_idx = np.arange(left_bound, right_bound + 1)
        local_vals = values[local_idx]
        valid_mask = ~np.isnan(local_vals)

        if valid_mask.sum() < 2:
            continue

        kind_map = {"linear": "linear", "quadratic": "quadratic", "cubic": "cubic"}
        kind = kind_map[method]
        min_points_needed = {"linear": 2, "quadratic": 3, "cubic": 4}[method]
        if valid_mask.sum() < min_points_needed:
            kind = "linear"
            method = "linear"

        try:
            f = interp1d(local_idx[valid_mask], local_vals[valid_mask], kind=kind,
                         bounds_error=False, fill_value="extrapolate")
            gap_idx = np.arange(start, end + 1)
            filled[gap_idx] = f(gap_idx)
            method_flags[gap_idx] = method
        except Exception:
            f = interp1d([left_idx, right_idx], [values[left_idx], values[right_idx]],
                         kind="linear", bounds_error=False, fill_value="extrapolate")
            gap_idx = np.arange(start, end + 1)
            filled[gap_idx] = f(gap_idx)
            method_flags[gap_idx] = "linear"

    return pd.Series(filled, index=series.index), pd.Series(method_flags, index=series.index)
