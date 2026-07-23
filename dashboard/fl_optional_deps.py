"""
fl_optional_deps.py -- Central place to import optional heavy dependencies.

If xgboost / scikit-learn aren't installed, every tab that uses them should
degrade gracefully (skip that specific method, not crash the whole app).
"""

import sys
import warnings

HAS_XGBOOST = False
HAS_SKLEARN = False
XGBOOST_VERSION = None
XGBOOST_ERR = None
SKLEARN_ERR = None

try:
    import xgboost as xgb  # noqa: F401
    HAS_XGBOOST = True
    XGBOOST_VERSION = xgb.__version__
except ImportError as e:
    xgb = None
    XGBOOST_ERR = str(e)
    warnings.warn(f"xgboost not available: {e}")
except Exception as e:
    xgb = None
    XGBOOST_ERR = str(e)
    warnings.warn(f"xgboost import failed: {e}")

try:
    from sklearn.linear_model import LinearRegression  # noqa: F401
    from sklearn.metrics import mean_squared_error  # noqa: F401
    HAS_SKLEARN = True
except ImportError as e:
    LinearRegression = None
    mean_squared_error = None
    SKLEARN_ERR = str(e)
    warnings.warn(f"scikit-learn not available: {e}")
except Exception as e:
    LinearRegression = None
    mean_squared_error = None
    SKLEARN_ERR = str(e)
    warnings.warn(f"scikit-learn import failed: {e}")
