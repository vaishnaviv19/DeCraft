# tests/generation/test_chart_kind.py
import pandas as pd

from src.contracts.dataset import empty_dataset
from src.contracts.report import FigureInfo
from src.generation._charts import choose_chart_kind, register_all_charts


def dataset(**frames) -> object:
    data = empty_dataset()
    data.tables = {name: frame for name, frame in frames.items()}
    data.display = {name: frame.copy() for name, frame in frames.items()}
    return data


def test_many_periods_are_a_line_and_few_are_bars():
    long = dataset(t=pd.DataFrame({"Item": list("ABCDEFGH"),
                                   **{f"FY{20 + i}": [1.0] * 8 for i in range(8)}}))
    assert choose_chart_kind(long, "t") == "line"
    short = dataset(t=pd.DataFrame({"Item": list("ABCDE"),
                                    **{f"FY{20 + i}": [1.0] * 5 for i in range(5)}}))
    assert choose_chart_kind(short, "t") == "bar"


def test_shares_summing_to_a_hundred_are_a_donut():
    data = dataset(t=pd.DataFrame({"Segment": ["A", "B", "C"], "Share": [30.0, 30.0, 40.0]}))
    assert choose_chart_kind(data, "t") == "donut"


def test_categories_are_a_horizontal_bar():
    data = dataset(t=pd.DataFrame({"Region": list("ABCDEF"), "Value": [1.0] * 6}))
    assert choose_chart_kind(data, "t") == "horizontal_bar"


def test_the_source_chart_kind_wins_over_the_rules():
    data = dataset(t=pd.DataFrame({"Region": list("ABCDEF"), "Value": [1.0] * 6}))
    figure = FigureInfo(figure_id="p1b01", kind="chart", chart_kind="pie")
    assert choose_chart_kind(data, "t", figure) == "pie"


def test_every_displayed_table_gets_exactly_one_chart():
    data = dataset(a=pd.DataFrame({"Region": list("ABCDEF"), "Value": [1.0] * 6}),
                   b=pd.DataFrame({"Item": list("ABCDE"),
                                   **{f"FY{20 + i}": [1.0] * 5 for i in range(5)}}))
    charts = register_all_charts(data, None)
    assert {spec.table_id for spec in charts.values()} == {"a", "b"}
    assert len(charts) == 2
