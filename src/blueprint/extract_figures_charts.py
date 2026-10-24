# src/blueprint/extract_figures_charts.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from PIL import Image


def _classify_chart_heuristic(img: Image.Image) -> str:
    try:
        img = img.convert("RGB")
        arr = np.array(img)
        pixels = arr.reshape(-1, 3)
        if len(pixels) > 10000:
            idx = np.random.choice(len(pixels), 10000, replace=False)
            pixels = pixels[idx]
        uniq = len(set(map(tuple, pixels)))
        h, w = img.size[1], img.size[0]
        if h == 0 or w == 0:
            return "other"
        ratio = w / h if h else 0
        if 0.8 < ratio < 1.2 and uniq > 20:
            return "pie"
        if ratio > 1.3 and uniq > 25:
            return "bar"
        if uniq < 35:
            return "line"
        return "other"
    except Exception:
        return "other"


def extract_charts(figures_dir: Path, extraction_summary_path: Path | None = None) -> List[Dict[str, Any]]:
    """
    FR-A5: location, bounding box, classified chart type (bar, line, pie, other). Use heuristics or vision model.
    """
    out = []
    for idx, img_path in enumerate(sorted(figures_dir.glob("figure_*.png")), start=1):
        try:
            img = Image.open(img_path)
            w, h = img.size
            chart_type = _classify_chart_heuristic(img)
            img.close()
        except Exception:
            w, h, chart_type = 0, 0, "other"
        out.append(
            {
                "figure_id": f"figure_{idx:03d}",
                "image_path": str(img_path),
                "location": {"page": None},
                "bounding_box": {"x0": 0, "y0": 0, "x1": w, "y1": h},
                "chart_type": chart_type,
                "classification_method": "heuristic",
            }
        )
    return out
