# tests/inference/test_formula.py
import pandas as pd
import pytest

from src.errors import FormulaSyntaxError
from src.inference._formula import evaluate, parse_formula, verify_formula

COLS = ["Revenue", "Units", "Unit Price", "Cost", "Profit", "Margin %", "DOB", "Age"]


def test_parse_and_evaluate_product(sales_df):
    spec = parse_formula("[Revenue] = [Units] * [Unit Price]", list(sales_df.columns))
    assert spec.output == "Revenue" and set(spec.inputs) == {"Units", "Unit Price"}
    assert (evaluate(spec.expr, sales_df) == sales_df["Revenue"]).all()


def test_operand_order_is_kept_for_subtraction(sales_df):
    spec = parse_formula("[Profit] = [Revenue] - [Cost]", list(sales_df.columns))
    assert verify_formula(sales_df, spec)[0] == 1.0
    wrong = parse_formula("[Profit] = [Cost] - [Revenue]", list(sales_df.columns))
    assert verify_formula(sales_df, wrong)[0] == 0.0


@pytest.mark.parametrize("text", [
    '[Revenue] = __import__("os").system("x")',   # call of a non-allowlisted function
    "[Revenue] = [Units].real",                    # attribute access
    "[Revenue] = Units * 2",                       # bare name instead of [Column]
    "[Revenue] = [Ghost] * 2",                     # unknown column
    "[Revenue] = [Units] ** 2",                    # power is not allowed
    "[Revenue] = [Revenue] + 1",                   # self reference
    "[Revenue] = lambda: 1",
    "[Revenue] == [Units]",
])
def test_disallowed_constructs_raise(text):
    with pytest.raises(FormulaSyntaxError):
        parse_formula(text, COLS)


def test_constant_factor_and_functions():
    df = pd.DataFrame({"Amount": [100.0, 250.0, 80.0, 40.0, 10.0],
                       "Tax": [18.0, 45.0, 14.4, 7.2, 1.8]})
    spec = parse_formula("[Tax] = 0.18 * [Amount]", ["Amount", "Tax"])
    assert verify_formula(df, spec)[0] == 1.0


def test_division_by_zero_is_invalid_not_a_match():
    df = pd.DataFrame({"a": [1.0] * 5, "b": [0.0] * 5, "r": [1.0] * 5})
    rate, n, invalid = verify_formula(df, parse_formula("[r] = [a] / [b]", ["a", "b", "r"]))
    assert rate == 0.0 and invalid == 5


def test_display_rounding_is_tolerated():
    # Margin shown with 1 decimal; true value 33.333...
    df = pd.DataFrame({"Profit": [1, 2, 1, 5, 7.0], "Revenue": [3, 6, 3, 15, 21.0],
                       "Margin %": [33.3] * 5})
    spec = parse_formula("[Margin %] = ratio_pct([Profit], [Revenue])",
                         ["Profit", "Revenue", "Margin %"])
    assert verify_formula(df, spec, decimals={"Margin %": 1}, rel_tol=0.0)[0] == 1.0
    assert verify_formula(df, spec, decimals={"Margin %": 3}, rel_tol=0.0)[0] == 0.0


def test_too_few_rows_is_untestable():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [2.0, 4.0]})
    assert verify_formula(df, parse_formula("[b] = 2 * [a]", ["a", "b"]))[0] is None


def test_years_between_for_age():
    df = pd.DataFrame({"DOB": ["1990-03-01"] * 5, "Ref": ["2025-06-30"] * 5, "Age": [35] * 5})
    spec = parse_formula("[Age] = years_between([Ref], [DOB])", ["DOB", "Ref", "Age"])
    assert verify_formula(df, spec)[0] == 1.0
