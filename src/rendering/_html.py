# src/rendering/_html.py
"""HTML rendering (M8.5): Jinja2 templates, tables and charts read from the Display Tables."""
from __future__ import annotations

from jinja2 import Template

from src.contracts.dataset import CanonicalDataset

PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{{ title }}</title>
<meta name="decraft:synthetic" content="true">
<meta name="decraft:run_id" content="{{ run_id }}">
<style>
body { font-family: "Segoe UI", Arial, sans-serif; color: #1a1a1a; margin: 2rem; }
h1 { font-size: 20pt; } h2 { font-size: 14pt; margin-top: 1.4rem; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; }
th, td { border: 1px solid #bbb; padding: 4px 6px; text-align: right; }
th:first-child, td:first-child { text-align: left; }
figure { margin: 1rem 0; } img { max-width: 100%; }
</style></head><body>
<h1>{{ title }}</h1>
{% for section in sections %}
<section><h2>{{ section.title }}</h2><p>{{ section.text }}</p></section>
{% endfor %}
{% for table in tables %}
<h2>{{ table.title }}</h2>
{{ table.html | safe }}
{% endfor %}
{% for chart in charts %}
<figure><img src="{{ chart.src }}" alt="{{ chart.title }}"><figcaption>{{ chart.title }}</figcaption></figure>
{% endfor %}
</body></html>
""")


def build_html(data: CanonicalDataset, narrative, blueprint=None) -> str:
    title = "DeCraft report"
    if isinstance(blueprint, dict):
        entity = (blueprint.get("entity") or {}).get("entity_name")
        title = entity or title
    tables = []
    for table_id, display in data.display.items():
        tables.append({"title": table_id,
                       "html": display.to_html(index=False, border=0, na_rep="", escape=True)})
    charts = [{"src": f"charts/{chart_id}.svg", "title": chart_id} for chart_id in data.charts]
    return PAGE.render(title=title, run_id=data.dataset_id, sections=narrative.sections,
                       tables=tables, charts=charts)
