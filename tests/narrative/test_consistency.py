# tests/narrative/test_consistency.py
import json


from src.config import load_config
from src.contracts.report import BibleEntity, StoryBible
from src.narrative._claims import Claim, Entity
from src.narrative._consistency import (ReconcileOut, merge, reconcile, rename_new,
                                        rewrite_guards)
from tests.conftest import FakeLLM

CFG = load_config(llm_enabled=False)


def bible() -> StoryBible:
    return StoryBible(version=1, entities=[
        BibleEntity(id="E1", kind="initiative", name="Project Meridian", attrs={"year": "FY24"}),
        BibleEntity(id="E2", kind="place", name="Meridian Campus", attrs={})])


def test_new_ids_are_namespaced_by_section():
    renamed = rename_new("S4", [Entity("N1", "initiative", "Orion"), Entity("N2", "place", "Hosur")])
    assert [entity.id for entity in renamed] == ["N:S4:1", "N:S4:2"]
    assert all(entity.introduced_in == "S4" for entity in renamed)


def test_conflicting_and_unknown_claims_are_detected():
    claims = [Claim(section="S1", paragraph=0, entity="E1", attr="year", value="FY24", block="p1b01"),
              Claim(section="S2", paragraph=0, entity="E1", attr="year", value="FY25", block="p2b01"),
              Claim(section="S3", paragraph=0, entity="E9", attr="year", value="FY25", block="p3b01")]
    entities, conflicts, unknown = merge(bible(), claims, [])
    assert len(conflicts) == 1 and set(conflicts[0].values) == {"FY24", "FY25"}
    assert len(unknown) == 1 and unknown[0].entity == "E9"


def test_guards_reject_invented_choices_and_unflagged_rewrites():
    claims = [Claim(section="S1", paragraph=0, entity="E1", attr="year", value="FY24", block="p1b01"),
              Claim(section="S2", paragraph=0, entity="E1", attr="year", value="FY25", block="p2b01")]
    _, conflicts, _ = merge(bible(), claims, [])
    conflict = conflicts[0]
    texts = {"p1b01": "Meridian launched in FY24.", "p2b01": "Meridian launched in FY25."}
    contexts = {"p1b01": texts["p1b01"],
                "p2b01": texts["p2b01"]}
    invented = ReconcileOut(choice="FY27")
    assert any("not one of" in error for error in rewrite_guards(
        conflict, invented, texts, contexts, {"Project Meridian"}, ["FY24", "FY25"], {}))
    elsewhere = ReconcileOut(choice="FY25", rewrites=[
        {"block_id": "p3b01", "text": "Meridian launched in FY25."}])
    assert any("unflagged block" in error for error in rewrite_guards(
        conflict, elsewhere, texts, contexts, {"Project Meridian"}, ["FY24", "FY25"], {}))
    missing_value = ReconcileOut(choice="FY25", rewrites=[
        {"block_id": "p1b01", "text": "Meridian launched later."}])
    assert any("verbatim" in error for error in rewrite_guards(
        conflict, missing_value, texts, contexts, {"Project Meridian"}, ["FY24", "FY25"], {}))


def test_a_valid_reconciliation_updates_the_bible_and_the_text(gateway_for):
    claims = [Claim(section="S1", paragraph=0, entity="E1", attr="year", value="FY24", block="p1b01"),
              Claim(section="S2", paragraph=0, entity="E1", attr="year", value="FY25", block="p2b01")]
    entities, conflicts, _ = merge(bible(), claims, [])
    texts = {"p1b01": "Meridian launched in FY24.", "p2b01": "Meridian launched in FY25."}
    contexts = {"p1b01": texts["p1b01"], "p2b01": texts["p2b01"]}
    assert not rewrite_guards(conflicts[0], ReconcileOut(choice="FY25", rewrites=[
        {"block_id": "p1b01", "text": "Meridian launched in FY25."}]),
        texts, contexts, {"Project Meridian"}, ["FY24", "FY25"], {})
    from src.narrative._consistency import reconcile as run
    reply = json.dumps({"choice": "FY25", "reason": "latest timeline",
                        "rewrites": [{"block_id": "p1b01",
                                      "text": "Meridian launched in FY25."}]})
    _, updated, stats = run(entities, conflicts, dict(texts), CFG,
                            gateway_for(FakeLLM([reply])),
                            ["FY24", "FY25"], {}, {"Project Meridian"})
    assert stats["resolved"] == 1 and "FY25" in updated["p1b01"]
    assert entities["E1"].attrs["year"] == "FY25"


def test_unresolved_conflicts_drop_the_sentences():
    claims = [Claim(section="S1", paragraph=0, entity="E1", attr="year", value="FY24", block="p1b01"),
              Claim(section="S2", paragraph=0, entity="E1", attr="year", value="FY25", block="p2b01")]
    entities, conflicts, _ = merge(bible(), claims, [])
    texts = {"p1b01": "Meridian launched in FY24. It grew.", "p2b01": "Meridian launched in FY25."}
    _, updated, stats = reconcile(entities, conflicts, texts, CFG, None, ["FY24", "FY25"], {},
                                  {"Project Meridian"})
    assert stats["final_conflicts"] == 1 and stats["dropped"] >= 1
    assert "FY24" not in updated["p1b01"] and "It grew." in updated["p1b01"]


def test_a_rewrite_may_not_touch_an_unflagged_paragraph():
    claims = [Claim(section="S1", paragraph=1, entity="E1", attr="year", value="FY24", block="p1b01"),
              Claim(section="S2", paragraph=0, entity="E1", attr="year", value="FY25", block="p2b01")]
    _, conflicts, _ = merge(bible(), claims, [])
    texts = {"p1b01": "An introduction that must not change.\n\nMeridian launched in FY24.",
             "p2b01": "Meridian launched in FY25."}
    contexts = {"p1b01": texts["p1b01"], "p2b01": texts["p2b01"]}
    reply = ReconcileOut(choice="FY25", rewrites=[
        {"block_id": "p1b01", "paragraph": 1,
         "text": "A different introduction.\n\nMeridian launched in FY25."}])
    errors = rewrite_guards(conflicts[0], reply, texts, contexts, {"Project Meridian"},
                            ["FY24", "FY25"], {})
    assert any("unflagged paragraph" in error for error in errors)


def test_a_rewrite_that_keeps_every_unflagged_paragraph_is_accepted():
    claims = [Claim(section="S1", paragraph=1, entity="E1", attr="year", value="FY24", block="p1b01"),
              Claim(section="S2", paragraph=0, entity="E1", attr="year", value="FY25", block="p2b01")]
    _, conflicts, _ = merge(bible(), claims, [])
    texts = {"p1b01": "An introduction.\n\nMeridian launched in FY24.",
             "p2b01": "Meridian launched in FY25."}
    contexts = {"p1b01": texts["p1b01"], "p2b01": texts["p2b01"]}
    reply = ReconcileOut(choice="FY25", rewrites=[
        {"block_id": "p1b01", "paragraph": 1, "text": "An introduction.\n\nMeridian launched in FY25."}])
    assert rewrite_guards(conflicts[0], reply, texts, contexts, {"Project Meridian"},
                          ["FY24", "FY25"], {}) == []
