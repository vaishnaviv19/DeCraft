# tests/inference/test_relationships.py
import pandas as pd

from src.contracts import Cardinality
from src.inference._relationships import candidate_keys, detect_relationships


def test_primary_key_on_the_sales_fixture(sales_df):
    assert candidate_keys(sales_df) == ["Ref"]


def test_unique_text_column_without_identifier_shape_is_not_a_key():
    df = pd.DataFrame({"Region": ["North", "South", "East"]})
    assert candidate_keys(df) == []


def test_a_period_column_is_a_time_axis_not_a_key():
    df = pd.DataFrame({"Period": ["FY21A", "FY22A", "FY23A"], "Revenue": [1.0, 2.0, 3.0]})
    assert candidate_keys(df) == []


def test_duplicate_values_disqualify_a_key():
    df = pd.DataFrame({"Code": ["A-1", "A-2", "A-1"]})
    assert candidate_keys(df) == []


def test_foreign_key_is_found_by_value_overlap(cfg):
    customers = pd.DataFrame({"customer_id": ["C-101", "C-102", "C-103"],
                              "name": ["Ann", "Bob", "Cid"]})
    orders = pd.DataFrame({"order_id": ["O-101", "O-102", "O-103", "O-104", "O-105", "O-106"],
                           "customer_id": ["C-101", "C-102", "C-101", "C-103", "C-102", "C-101"],
                           "amount": [10, 20, 30, 40, 50, 60]})
    rels = detect_relationships({"customers": customers, "orders": orders}, cfg.thresholds)
    assert len(rels) == 1
    rel = rels[0]
    assert (rel.parent.node, rel.child.node) == ("customers.customer_id", "orders.customer_id")
    assert rel.cardinality is Cardinality.ONE_TO_MANY and rel.overlap_rate == 1.0


def test_similar_names_alone_never_create_a_relationship(cfg):
    left = pd.DataFrame({"customer_id": ["A1", "A2", "A3", "A4", "A5"]})
    right = pd.DataFrame({"customer_id": ["B1", "B2", "B3", "B4", "B5"]})
    assert detect_relationships({"left": left, "right": right}, cfg.thresholds) == []


def _customers_orders(order_ids):
    customers = pd.DataFrame({"Customer ID": [101, 102, 103], "name": ["Ann", "Bob", "Cid"]})
    orders = pd.DataFrame({"order_id": ["O-1", "O-2", "O-3", "O-4", "O-5", "O-6"], "customer_id": order_ids})
    return {"customers": customers, "orders": orders}


def test_integer_and_float_forms_of_a_key_still_match(cfg):
    rels = detect_relationships(_customers_orders([101.0, 102.0, 101.0, 103.0, 102.0, 101.0]), cfg.thresholds)
    assert [r.child.node for r in rels] == ["orders.customer_id"] and rels[0].overlap_rate == 1.0


def test_doubtful_table_caps_a_link_below_accepted(cfg):
    from src.contracts import DecisionStatus, ExtractionGrade
    tables = _customers_orders([101, 102, 101, 103, 102, 101])
    rels = detect_relationships(tables, cfg.thresholds, {"orders": ExtractionGrade.DOUBTFUL})
    assert rels and rels[0].status is DecisionStatus.NEEDS_REVIEW
