# tests/test_schema.py
from src.inference.schema import Evidence, FieldProfile, RuleKind, InferenceReport, DomainHypothesis


def test_roundtrip():
    ev = [Evidence(source="header", detail="found", weight=0.9)]
    fp = FieldProfile(
        table="t1",
        name="revenue",
        physical_type="float",
        semantic_type="currency",
        n=10,
        missing_rate=0.0,
        n_unique=10,
        min=0,
        max=100,
        unit="USD",
        fmt="$",
        confidence=0.95,
        evidence=ev,
    )
    report = InferenceReport(
        source_pdf="test.pdf",
        source_hash="abc123",
        blueprint_version="1.0",
        seed=12345,
        domain=DomainHypothesis(label="finance", confidence=0.8, evidence=ev),
        fields=[fp],
    )
    data = report.model_dump()
    report2 = InferenceReport.model_validate(data)
    assert report2.source_pdf == "test.pdf"
    assert report2.fields[0].semantic_type == "currency"
    print("OK")
