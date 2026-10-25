# tests/inference/test_profile.py
from src.contracts import ExtractionGrade, MissingKind, SourceTable, TableQuality
from src.inference._profile import profile_table


def profiles(header, rows, cfg):
    t = SourceTable(table_id="t1", page=1, header=header, raw_cells=rows)
    got, warnings = profile_table(t, TableQuality(grade=ExtractionGrade.GOOD), cfg)
    return {p.column: p for p in got}, warnings


def test_currency_column_gets_usd_two_decimals_and_base_values(cfg):
    p, _ = profiles(["Company", "Revenue"],
                    [["Acme", "$1,250.40"], ["Beta", "$2,000.00"]], cfg)
    rev = p["Revenue"]
    assert (rev.semantic_type, rev.unit, rev.decimals, rev.scale) == ("currency", "USD", 2, 1.0)
    assert rev.numeric.minimum == 1250.40 and rev.numeric.maximum == 2000.0
    assert rev.additive is None


def test_percentage_is_never_scaled(cfg):
    p, _ = profiles(["Growth"], [["12.5%"], ["10.0%"]], cfg)
    g = p["Growth"]
    assert (g.semantic_type, g.unit, g.decimals, g.scale) == ("percentage", "%", 1, 1.0)
    assert g.numeric.maximum == 12.5


def test_identifier_column_is_detected(cfg):
    p, _ = profiles(["Ref"], [["A-101"], ["A-102"], ["A-103"]], cfg)
    assert p["Ref"].semantic_type == "identifier"


def test_count_column_is_detected(cfg):
    p, _ = profiles(["Units"], [["10"], ["12"], ["9"]], cfg)
    u = p["Units"]
    assert (u.semantic_type, u.physical_type, u.additive) == ("count", "integer", None)


def test_scale_is_applied_to_currency_only(cfg):
    p, _ = profiles(["Revenue (Rs. cr)"], [["1,234"], ["2,500"]], cfg)
    r = p["Revenue (Rs. cr)"]
    assert r.scale == 1e7 and r.semantic_type == "currency"
    assert r.numeric.maximum == 2500 * 1e7


def test_missing_kinds_are_counted(cfg):
    p, _ = profiles(["Value"], [["1"], ["-"], ["n/a"], [""]], cfg)
    v = p["Value"]
    assert v.n == 4 and v.n_missing == 3 and v.n_unique == 1
    assert v.missing_kinds == {MissingKind.DASH: 1, MissingKind.NA: 1, MissingKind.NULL: 1}


def test_date_column_gets_date_stats(cfg):
    p, _ = profiles(["Date"], [["2021-03-31"], ["2023-03-31"]], cfg)
    d = p["Date"]
    assert d.physical_type == "date" and d.semantic_type == "date"
    assert d.dates.earliest.isoformat() == "2021-03-31"
    assert d.dates.latest.isoformat() == "2023-03-31"


def test_period_column_is_detected(cfg):
    p, _ = profiles(["Period"], [["Q1FY24"], ["Q2FY24"]], cfg)
    assert p["Period"].physical_type == "period"


def test_mixed_formats_are_flagged_below_escalation(cfg):
    p, warnings = profiles(["Units"], [["10"], ["12"], ["n/a"], ["x"], ["14"]], cfg)
    u = p["Units"]
    assert u.llm_confidence is not None
    assert u.llm_confidence < cfg.thresholds.llm_confidence_escalate
    assert any("Units" in w and "mixed" in w.lower() for w in warnings)


def test_empty_column_is_empty_type(cfg):
    p, _ = profiles(["Notes"], [[""], [""]], cfg)
    assert p["Notes"].physical_type == "empty" and p["Notes"].n_missing == 2


def test_label_column_containing_a_percent_word_is_not_percentage(cfg):
    """A transposed table's label column must not be typed from a label that happens to contain %."""
    p, _ = profiles(["Company Data"], [["Dividend Yield"], ["Shareholding %"], ["Beta"]], cfg)
    c = p["Company Data"]
    assert c.physical_type == "text" and c.semantic_type != "percentage"


def test_category_column_is_detected(cfg):
    p, _ = profiles(["Region"], [["North"], ["South"], ["North"]], cfg)
    assert p["Region"].semantic_type == "category"


def test_period_labels_in_a_transposed_column_do_not_raise_a_mixed_warning(cfg):
    _, warnings = profiles(["Values"], [["53.0%"], ["Q3FY23"], ["1.0"]], cfg)
    assert warnings == []


def test_one_identifier_shaped_value_is_not_enough(cfg):
    p, _ = profiles(["Label"], [["Q3FY23"], ["Q4FY23"], ["Other"], ["Total"]], cfg)
    assert p["Label"].semantic_type != "identifier"


def test_basis_points_parse_as_numbers(cfg):
    p, warnings = profiles(["YoY Growth %"], [["850bps"], ["(140bps)"], ["5.0"]], cfg)
    g = p["YoY Growth %"]
    assert g.semantic_type == "percentage" and g.unit == "bps"
    assert g.numeric.minimum == -140.0 and g.numeric.maximum == 850.0
    assert warnings == []


def test_value_per_share_is_currency_not_percentage(cfg):
    p, _ = profiles(["Value Per Share (Rs.)"], [["51"], ["4"]], cfg)
    v = p["Value Per Share (Rs.)"]
    assert v.semantic_type == "currency" and v.unit == "INR"


def test_category_columns_are_never_scaled(cfg):
    p, _ = profiles(["Methodology"], [["P/BV"], ["P/E"]], cfg)
    assert p["Methodology"].scale == 1.0


def test_table_header_scale_applies_to_plain_number_columns(cfg):
    """The table states Rs cr in its header row, so every plain number column is in base units."""
    table = SourceTable(table_id="t1", page=3, header=["Y.E March (Rs. cr)", "FY21A"],
                        raw_cells=[["Revenue", "32,703"], ["EBITDA", "3,215"]],
                        context_text="PROFIT & LOSS")
    got, _ = profile_table(table, TableQuality(grade=ExtractionGrade.GOOD), cfg)
    col = next(p for p in got if p.column == "FY21A")
    assert col.scale == 1e7 and col.unit == "INR" and col.semantic_type == "currency"
    assert col.numeric.maximum == 32703 * 1e7


def test_cells_with_their_own_currency_do_not_take_the_table_scale(cfg):
    table = SourceTable(table_id="t1", page=1, header=["Company Data"],
                        raw_cells=[["Rs. 86,856"], ["Rs. 1,370"]])
    got, _ = profile_table(table, TableQuality(grade=ExtractionGrade.GOOD), cfg)
    assert got[0].scale == 1.0 and got[0].semantic_type == "currency"
    assert got[0].unit == "INR"


def test_multiples_do_not_take_the_table_scale(cfg):
    table = SourceTable(table_id="t1", page=4, header=["SOTP (Rs. cr)", "Value", "Multiple"],
                        raw_cells=[["Equity", "13,926", "3.5x"], ["Cash", "1,370", "2.5x"]])
    got, _ = profile_table(table, TableQuality(grade=ExtractionGrade.GOOD), cfg)
    by_col = {p.column: p for p in got}
    assert by_col["Value"].scale == 1e7 and by_col["Value"].semantic_type == "currency"
    assert by_col["Multiple"].scale == 1.0 and by_col["Multiple"].semantic_type == "numeric"
