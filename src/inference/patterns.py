# src/inference/patterns.py
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
from scipy.stats import kendalltau, theilslopes


def classify_trend(y, tau_min: float = 0.5, p_max: float = 0.10, min_n: int = 4):
    y = np.asarray(y, float)
    if len(y) < min_n:
        return "uncertain", None
    x = np.arange(len(y))
    try:
        tau, p = kendalltau(x, y)
    except Exception:
        return "uncertain", None
    try:
        slope = theilslopes(y, x)[0]
    except Exception:
        slope = None
    if p <= p_max and tau >= tau_min:
        return "increasing", slope
    if p <= p_max and tau <= -tau_min:
        return "decreasing", slope
    if abs(tau) < 0.2:
        return "flat", slope
    return "mixed", slope
