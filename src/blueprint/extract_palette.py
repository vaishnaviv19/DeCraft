# src/blueprint/extract_palette.py
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans


def extract_palette(text_blocks: List[Dict[str, Any]], figures_dir: Path) -> Dict[str, Any]:
    """
    FR-A6: Extract the colour palette (dominant and accent colours) from vector fills, text colours, and rasterized page samples.
    """
    colors = []
    for b in text_blocks:
        c = b.get("color")
        if c and c != "#000000":
            colors.append(c)
    for img_path in sorted(figures_dir.glob("figure_*.png"))[:15]:
        try:
            img = Image.open(img_path).convert("RGB")
            arr = np.array(img)
            pixels = arr.reshape(-1, 3)
            if len(pixels) > 5000:
                idx = np.random.choice(len(pixels), 5000, replace=False)
                pixels = pixels[idx]
            kmeans = KMeans(n_clusters=3, n_init=5, random_state=42)
            kmeans.fit(pixels)
            for center in kmeans.cluster_centers_:
                r, g, b = int(center[0]), int(center[1]), int(center[2])
                colors.append(f"#{r:02X}{g:02X}{b:02X}")
            img.close()
        except Exception:
            continue
    if not colors:
        return {"dominant": ["#000000"], "accent": [], "source_counts": {}}
    cnt = Counter(colors)
    common = cnt.most_common(12)
    dominant = [c for c, _ in common[:4]]
    accent = [c for c, _ in common[4:8]]
    return {"dominant": dominant, "accent": accent, "source_counts": dict(common)}
