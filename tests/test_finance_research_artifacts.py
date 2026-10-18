import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from policy_impact.domains.finance.article_corpus import ArticleCorpus
from policy_impact.domains.finance.article_study import StudyBatch
from policy_impact.domains.finance.research_artifacts import _horizon_end
from policy_impact.domains.finance.research_gateway import FinanceResearchGateway
from policy_impact.domains.finance.research_verification import TypedReviewBatch
from policy_impact.domains.finance.structured_pass import complete_structured
from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.runtime.plugin_config import read_plugin_toolkit


@pytest.fixture
def research(tmp_path, monkeypatch):
    from policy_impact.company_wiki import loader

    monkeypatch.setattr(loader, "project_root", lambda: tmp_path)
    root = tmp_path / "articles"
    for author, published_at, view in (
        ("TT", "2026-09-28", "Gold rises when real yields fall."),
        ("Other", "2026-09-20", "Gold may weaken if the dollar rises."),
    ):
        folder = root / author / "articles"
        folder.mkdir(parents=True)
        (folder / f"{published_at}-note.md").write_text(
            f"# {author} note\n{published_at}\n{view}\nThis is a condition, not a certainty.\n",
            encoding="utf-8",
        )
    corpus = ArticleCorpus("finance_observer", root)
    gateway = FinanceResearchGateway(
        "finance_observer", set(), question="Compare gold views", corpus=corpus,
        session_id="session-one", run_id="run_12345678", child_roles=("analyst",),
    )
    return gateway, root


def _card(gateway, article_id, read, quote, *, depth="skim"):
    return gateway.call(
        "chat_agent_loop", "research.artifact", operation="save", kind="article_card",
        payload={
            "article_id": article_id, "question": "What is the gold view?", "depth": depth,
            "claims": [{
                "kind": "author_view", "statement": quote, "quote": quote,
                "line_start": 3, "line_end": 3, "evidence_id": read["evidence_id"],
            }],
        },
    )


def _retain_relation_review(messages):
    return {"judgments": [
        {"relation_index": row["relation_index"], "verdict": "retain",
         "reason": "The quoted claims support this bounded cross-article relation."}
        for row in messages[1]["content"]["relations"]
    ]}


@pytest.fixture
def study_output():
    return {"claims": [{
        "kind": "author_view", "statement": "Gold may rise as real yields fall.",
        "quote": "Gold rises when real yields fall.", "line_start": 3, "line_end": 3,
        "claim_confidence_label": "low",
    }], "limitations": "Only the supplied passage was studied."}


def _assert_output_contract(messages, schema):
    content = next(
        message["content"] for message in messages
        if message["role"] == "system" and isinstance(message["content"], str)
        and "output_json_schema" in message["content"]
    )
    assert '{"type":"final","output":...}' in content
    assert "only the value of output, not the outer type/output envelope" in content
    contract = json.loads(content.rsplit("\n", 1)[1])
    assert contract == {"output_json_schema": schema.model_json_schema()}
    return contract["output_json_schema"]


@pytest.mark.parametrize("system_index", [0, 1])
def test_structured_model_pass_exposes_nested_contract_on_first_request(
    study_output, system_index,
):
    messages = [
        {"role": "user", "content": {
            "question": "What is the conditional view?",
            "source_lines": "3: Gold rises when real yields fall.",
        }},
        {"role": "user", "content": "Keep the source attribution."},
    ]
    messages.insert(system_index, {"role": "system", "content": "Study only this source."})
    messages.append({"role": "system", "content": "Do not treat views as verified facts."})
    original = deepcopy(messages)
    calls = []

    def complete(request, schema_name):
        calls.append(deepcopy(request))
        assert schema_name == "finance_article_study.v1"
        output_schema = _assert_output_contract(request, StudyBatch)
        assert output_schema["additionalProperties"] is False
        assert output_schema["properties"]["claims"]["items"] == {"$ref": "#/$defs/StudyClaim"}
        claim_schema = output_schema["$defs"]["StudyClaim"]
        assert claim_schema["additionalProperties"] is False
        assert set(claim_schema["required"]) == {
            "kind", "statement", "quote", "line_start", "line_end",
        }
        assert claim_schema["properties"]["line_start"]["minimum"] == 1
        assert claim_schema["properties"]["statement"]["minLength"] == 8
        assert claim_schema["properties"]["claim_confidence_label"]["enum"] == [
            "high", "medium", "low",
        ]
        return study_output

    result = complete_structured(complete, messages, "finance_article_study.v1", StudyBatch)
    assert result == StudyBatch.model_validate(study_output)
    assert len(calls) == 1
    assert messages == original
    for index, message in enumerate(original):
        if index == system_index:
            assert calls[0][index]["content"].startswith(message["content"] + "\n\n")
        else:
            assert calls[0][index] == message


@pytest.mark.parametrize("messages", [
    [],
    [{"role": "user", "content": {"source_lines": "3: A bounded source view."}}],
    [{"role": "user", "content": "source"},
     {"role": "assistant", "content": "previous response"},
     {"role": "user", "content": "Keep the attribution."}],
    [{"role": "system", "content": {"instruction": "Keep this structured context."}},
     {"role": "user", "content": "source"}],
])
def test_structured_model_pass_inserts_contract_without_reordering_messages(messages, study_output):
    original = deepcopy(messages)
    calls = []

    def complete(request, schema_name):
        calls.append(deepcopy(request))
        _assert_output_contract(request, StudyBatch)
        assert request[0]["role"] == "system"
        assert request[1:] == original
        return study_output

    complete_structured(complete, messages, "finance_article_study.v1", StudyBatch)
    assert len(calls) == 1
    assert messages == original


@pytest.mark.parametrize("failure", ["adapter", "shape", "semantic"])
def test_structured_model_pass_keeps_contract_in_each_recovery(study_output, failure):
    messages = [{"role": "system", "content": "Study this source only."},
                {"role": "user", "content": {"source_lines": "3: The actual source."}}]
    original = deepcopy(messages)
    calls = []

    def complete(request, schema_name):
        calls.append(deepcopy(request))
        _assert_output_contract(request, StudyBatch)
        assert request[1] == original[1]
        if len(calls) == 1:
            if failure == "adapter":
                raise ModelInvocationError("fixture malformed JSON", structured_output=True)
            if failure == "shape":
                return {"claims": "not an array"}
        return study_output

    def validate(batch):
        if failure == "semantic" and len(calls) == 1:
            raise ValueError("fixture evidence was not supplied")

    complete_structured(
        complete, messages, "finance_article_study.v1", StudyBatch, validate=validate,
    )
    assert len(calls) == 2
    feedback = calls[1][-1]["content"]
    assert feedback["validation_errors"]
    assert feedback["required_output_fields"] == list(StudyBatch.model_fields)
    _assert_output_contract(calls[1], StudyBatch)
    assert messages == original


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_structured_model_pass_keeps_contract_in_normalization(study_output, repair_succeeds):
    messages = [{"role": "user", "content": {"source_lines": "3: The actual source."}}]
    original = deepcopy(messages)
    calls = []

    def complete(request, schema_name):
        calls.append((deepcopy(request), schema_name))
        _assert_output_contract(request, StudyBatch)
        assert request[1] == original[0]
        if repair_succeeds and len(calls) == 5:
            return study_output
        return {"claims": "not an array"}

    def run():
        return complete_structured(
            complete, messages, "finance_article_study.v1", StudyBatch,
            repair_schema_name="fixture.normalize.v1",
        )

    if repair_succeeds:
        assert run() == StudyBatch.model_validate(study_output)
    else:
        with pytest.raises(ValueError, match="after 3 attempts plus 2 normalization attempts"):
            run()
    assert [schema_name for _, schema_name in calls] == [
        "finance_article_study.v1",
    ] * 3 + ["fixture.normalize.v1"] * 2
    for request, _ in calls[1:]:
        feedback = request[-1]["content"]
        assert feedback["required_output_fields"] == list(StudyBatch.model_fields)
        _assert_output_contract(request, StudyBatch)
    assert calls[3][0][-1]["content"]["repair_mode"] == "review.normalize"
    assert messages == original


@pytest.mark.parametrize("field,alias", [
    ("line_start", "source_line_start"),
    ("statement", "faithful_statement"),
])
def test_structured_model_pass_rejects_nested_field_aliases(study_output, field, alias):
    claim = study_output["claims"][0]
    claim[alias] = claim.pop(field)
    original = deepcopy(study_output)
    calls = []

    def complete(messages, schema_name):
        calls.append(deepcopy(messages))
        return study_output

    with pytest.raises(ValueError, match=f"claims.0.{field}: Field required"):
        complete_structured(complete, [{"role": "user", "content": "source"}],
                            "finance_article_study.v1", StudyBatch)
    assert len(calls) == 3
    assert study_output == original


@pytest.mark.parametrize("field,value", [
    ("claim_confidence_label", "certain"),
    ("quote", ["a quote must be a string"]),
    ("line_start", 0),
    ("statement", "short"),
    ("unexpected", "extra nested fields are forbidden"),
])
def test_structured_model_pass_rejects_nested_contract_violations(study_output, field, value):
    study_output["claims"][0][field] = value
    with pytest.raises(ValueError, match=f"claims.0.{field}"):
        complete_structured(
            lambda messages, schema_name: study_output,
            [{"role": "user", "content": "source"}],
            "finance_article_study.v1", StudyBatch,
        )


def test_structured_model_pass_repairs_schema_without_coercing_evidence():
    calls = []

    def complete(messages, schema_name):
        calls.append((messages, schema_name))
        if len(calls) == 1:
            return {"checks": [{"claim_id": "claim_1", "claim_type": "author_view",
                                "status": "viewpoint_faithful", "evidence_refs": ["evidence_1"],
                                "conflicts": ["unsupported conflict"],
                                "alternative_explanations": [], "suggested_revision": "Keep attribution",
                                "as_of": "2026-10-08", "reason": "Author view is sourced."}],
                    "limitations": ["needs more evidence"]}
        return {"checks": [{"claim_id": "claim_1", "claim_type": "author_view",
                            "status": "viewpoint_faithful", "evidence_refs": ["evidence_1"],
                            "conflicts": [], "alternative_explanations": [],
                            "suggested_revision": "Keep attribution", "as_of": "2026-10-08",
                            "reason": "Author view is sourced."}],
                "limitations": "needs more evidence"}

    result = complete_structured(complete, [{"role": "user", "content": "source"}],
                                 "finance_typed_review.v1", TypedReviewBatch)
    assert len(calls) == 2
    assert calls[1][0][-1]["content"]["validation_errors"]
    assert result.checks[0].conflicts == []


def test_structured_model_pass_rejects_persistent_invalid_shape():
    with pytest.raises(ValueError, match="after 3 attempts"):
        complete_structured(
            lambda messages, schema_name: {"checks": [], "limitations": []},
            [{"role": "user", "content": "source"}],
            "finance_typed_review.v1", TypedReviewBatch,
        )


def test_structured_model_pass_retries_semantic_contract():
    calls = []

    def complete(messages, schema_name):
        calls.append(messages)
        return {"checks": [{
            "claim_id": "claim_1", "claim_type": "author_view",
            "status": "viewpoint_faithful", "evidence_refs": ["evidence_1"],
            "suggested_revision": "Keep attribution", "reason": "Original article said this.",
            "as_of": "2026-09-30" if len(calls) == 1 else "2026-10-08",
        }]}

    def validate(batch):
        if batch.checks[0].as_of != "2026-10-08":
            raise ValueError("checks[].as_of must equal 2026-10-08")

    batch = complete_structured(
        complete, [{"role": "user", "content": "source"}],
        "finance_typed_review.v1", TypedReviewBatch, validate=validate,
    )
    assert batch.checks[0].as_of == "2026-10-08"
    assert "2026-10-08" in str(calls[1][-1]["content"]["validation_errors"])


def test_context_open_is_scoped_to_current_run(research):
    gateway, _ = research
    saved = gateway.context_store.save({"tool_result": {"text": "original"}}, "article.read")
    restored = gateway.call(
        "chat_agent_loop", "context.open", context_id=saved["context_id"]
    )
    assert restored["content"] == {"tool_result": {"text": "original"}}
    other = FinanceResearchGateway(
        "finance_observer", set(), question="Other run", corpus=gateway.corpus,
        session_id="session-two", run_id="run_87654321",
    )
    with pytest.raises(FileNotFoundError):
        other.call("chat_agent_loop", "context.open", context_id=saved["context_id"])


def test_card_requires_observed_exact_quote_and_full_read_for_deep(research):
    gateway, _ = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    with pytest.raises(ValueError, match="must be read"):
        _card(gateway, article_id, {"evidence_id": "evidence_fake"}, "Gold rises")

    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=3)
    with pytest.raises(ValueError, match="all source lines"):
        _card(gateway, article_id, read, "Gold rises when real yields fall.", depth="deep")
    with pytest.raises(ValueError, match="claim_lines=3-3.*observed_text_prefix"):
        _card(gateway, article_id, read, "Gold always rises")
    with pytest.raises(ValueError, match="article_lines=1-4.*observed_ranges"):
        gateway.call(
            "chat_agent_loop", "research.artifact", operation="save", kind="article_card",
            payload={"article_id": article_id, "question": "Gold view", "depth": "skim",
                     "claims": [{"kind": "author_view", "statement": "Wrong range",
                                 "quote": "Gold", "line_start": 5, "line_end": 5,
                                 "evidence_id": read["evidence_id"]}]},
        )
    with pytest.raises(ValueError, match="evidence_id"):
        _card(gateway, article_id, {"evidence_id": "evidence_fake"}, "Gold rises when real yields fall.")

    saved = _card(gateway, article_id, read, "Gold rises when real yields fall.")
    loaded = gateway.call(
        "chat_agent_loop", "research.artifact", operation="get", artifact_id=saved["artifact_id"]
    )
    assert saved["artifact_id"].startswith("card_")
    assert loaded["payload"]["observed_lines"] == 3
    assert loaded["payload"]["claims"][0]["claim_id"].startswith("claim_")
    gateway.call("chat_agent_loop", "article.read", article_id=article_id, start_line=4, limit=1)
    deep = _card(gateway, article_id, read, "Gold rises when real yields fall.", depth="deep")
    assert gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=deep["artifact_id"])["payload"]["depth"] == "deep"


def test_card_wrong_read_segment_identifies_matching_evidence(research):
    gateway, _ = research
    listed = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"]
    article_id = listed[0]["article_id"]
    first = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=2)
    second = gateway.call(
        "chat_agent_loop", "article.read", article_id=article_id, start_line=3, limit=2,
    )
    with pytest.raises(ValueError, match=second["evidence_id"]):
        _card(gateway, article_id, first, "Gold rises when real yields fall.")


def test_comparison_and_typed_review_bind_saved_claims(research):
    gateway, _ = research
    cards = []
    for author, quote in (
        ("TT", "Gold rises when real yields fall."),
        ("Other", "Gold may weaken if the dollar rises."),
    ):
        article_id = gateway.call("chat_agent_loop", "article.list", author=author)["articles"][0]["article_id"]
        read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=4)
        saved = _card(gateway, article_id, read, quote, depth="deep")
        record = gateway.call("chat_agent_loop", "research.artifact", operation="get",
                              artifact_id=saved["artifact_id"])
        cards.append(record)
    ids = [card["artifact_id"] for card in cards]
    claim_ids = [card["payload"]["claims"][0]["claim_id"] for card in cards]
    comparison = gateway.call(
        "chat_agent_loop", "research.artifact", operation="save", kind="comparison",
        payload={"question": "Compare the gold theses", "card_ids": ids,
                 "relations": [{"type": "complementary", "claim_ids": claim_ids,
                                "analysis": "They name different conditions."}]},
    )
    assert comparison["relation_count"] == 1
    with pytest.raises(ValueError, match="selected articles"):
        gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="comparison",
                     payload={"question": "Bad comparison", "card_ids": ids,
                              "relations": [{"type": "agreement", "claim_ids": [claim_ids[0], "claim_fake"],
                                             "analysis": "Unsupported"}]})

    claim = cards[0]["payload"]["claims"][0]
    with pytest.raises(ValueError, match="checkable facts"):
        gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="review",
                     payload={"card_ids": [ids[0]], "checks": [{
                         "claim_id": claim["claim_id"], "claim_type": "author_view",
                         "status": "supported", "evidence_refs": [claim["evidence_id"]],
                         "reason": "Author said so", "suggested_revision": "Keep as author view",
                         "as_of": "2026-10-08",
                     }]})
    review = gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="review",
                          payload={"card_ids": [ids[0]], "checks": [{
                              "claim_id": claim["claim_id"], "claim_type": "author_view",
                              "status": "viewpoint_faithful",
                              "evidence_refs": [claim["evidence_id"]],
                              "reason": "Exact line in TT article",
                              "suggested_revision": "TT argues that gold may rise under this condition.",
                              "as_of": "2026-10-08",
                          }]})
    assert review["check_count"] == 1
    assert gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=review["artifact_id"])["payload"]["review_scope"] == "main_agent_recorded"


def test_synthesis_selects_question_relevant_claims_before_large_comparison(research):
    gateway, _ = research
    cards = []
    for author, quote in (
        ("TT", "Gold rises when real yields fall."),
        ("Other", "Gold may weaken if the dollar rises."),
    ):
        article_id = gateway.call("chat_agent_loop", "article.list", author=author)["articles"][0]["article_id"]
        read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=4)
        saved = gateway.call(
            "chat_agent_loop", "research.artifact", operation="save", kind="article_card",
            payload={"article_id": article_id, "question": "Compare gold", "depth": "deep",
                     "claims": [{"kind": "author_view", "statement": f"View {index}: {quote}",
                                 "quote": quote, "line_start": 3, "line_end": 3,
                                 "evidence_id": read["evidence_id"]}
                                for index in range(13)]},
        )
        cards.append(gateway.call(
            "chat_agent_loop", "research.artifact", operation="get",
            artifact_id=saved["artifact_id"],
        ))
    selected_ids = [card["payload"]["claims"][0]["claim_id"] for card in cards]
    stages = []

    def complete(messages, schema_name):
        stages.append(schema_name)
        if schema_name == "finance_synthesis_selection.v1":
            return {"claim_ids": selected_ids, "rationale": "One key view per article."}
        if schema_name == "finance_relation_review.v1":
            return _retain_relation_review(messages)
        views = messages[1]["content"]["article_views"]
        assert [len(view["claims"]) for view in views] == [1, 1]
        return {"relations": [{"type": "disagreement", "claim_ids": selected_ids,
                               "analysis": "The two articles name different gold conditions."}]}

    gateway.synthesis.complete = complete
    result = gateway.call(
        "chat_agent_loop", "research.synthesize", question="Compare gold",
        card_ids=[card["artifact_id"] for card in cards],
    )
    assert stages == ["finance_synthesis_selection.v1", "finance_article_synthesis.v1",
                      "finance_relation_review.v1"]
    assert result["selected_claim_count"] == 2
    assert result["relation_count"] == 1


def test_stale_source_run_isolation_and_subagent_boundary(research):
    gateway, root = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=4)
    card = _card(gateway, article_id, read, "Gold rises when real yields fall.", depth="deep")
    assert gateway.call("chat_agent_loop", "research.artifact", operation="list")["count"] == 1
    with pytest.raises(PermissionError):
        gateway.call("subagent.analyst", "research.artifact", operation="list")
    with pytest.raises(ValueError, match="generated"):
        gateway.call("chat_agent_loop", "research.artifact", operation="get", artifact_id="../other")

    separate = FinanceResearchGateway(
        "finance_observer", set(), question="New run", corpus=gateway.corpus,
        session_id="session-one", run_id="run_87654321",
    )
    with pytest.raises(FileNotFoundError):
        separate.call("chat_agent_loop", "research.artifact", operation="get",
                      artifact_id=card["artifact_id"])

    path = Path(root / "TT" / "articles" / "2026-09-28-note.md")
    path.write_text(path.read_text(encoding="utf-8") + "Later correction.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        gateway.call("chat_agent_loop", "research.artifact", operation="get",
                     artifact_id=card["artifact_id"])


def test_research_note_save_reload_and_source_version_invalidation(research):
    gateway, root = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=4)
    card = _card(gateway, article_id, read, "Gold rises when real yields fall.", depth="deep")
    claim = gateway.call("chat_agent_loop", "research.artifact", operation="get",
                         artifact_id=card["artifact_id"])["payload"]["claims"][0]
    payload = {
        "question": "How do the views change?",
        "purpose": "Retain an observed view while reading more articles",
        "entries": [{
            "type": "article_view", "article_id": article_id,
            "statement": "TT links gold to falling real yields.",
            "evidence_refs": [read["evidence_id"]],
            "claim_ids": [claim["claim_id"]],
            "conditions": "Real yields fall", "time_scope": "2026-09",
        }],
        "open_questions": ["Did real yields actually fall?"],
        "limitations": "This is an author view, not independent market verification.",
    }
    saved = gateway.call("chat_agent_loop", "research.artifact", operation="save",
                         kind="research_note", payload=payload)
    assert saved["artifact_id"].startswith("note_")
    assert saved["entry_count"] == 1 and saved["evidence_ref_count"] == 1
    note = gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=saved["artifact_id"])
    assert note["payload"]["entries"][0]["claim_ids"] == [claim["claim_id"]]
    listing = gateway.call("chat_agent_loop", "research.artifact", operation="list",
                           kind="research_note")
    assert listing["count"] == 1

    separate = FinanceResearchGateway(
        "finance_observer", set(), question="Other run", corpus=gateway.corpus,
        session_id="session-two", run_id="run_87654321",
    )
    with pytest.raises(FileNotFoundError):
        separate.call("chat_agent_loop", "research.artifact", operation="get",
                      artifact_id=saved["artifact_id"])
    with pytest.raises(ValueError, match="current run"):
        separate.call("chat_agent_loop", "research.artifact", operation="save",
                      kind="research_note", payload=payload)
    with pytest.raises(PermissionError):
        gateway.call("subagent.analyst", "research.artifact", operation="get",
                     artifact_id=saved["artifact_id"])

    path = root / "TT" / "articles" / "2026-09-28-note.md"
    path.write_text(path.read_text(encoding="utf-8") + "Later correction.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        gateway.call("chat_agent_loop", "research.artifact", operation="get",
                     artifact_id=saved["artifact_id"])
    assert gateway.call("chat_agent_loop", "research.artifact", operation="list",
                        kind="research_note")["count"] == 0


def test_research_note_rejects_unbound_evidence_and_claim_ids(research):
    gateway, _ = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=4)
    payload = {
        "question": "Gold view", "purpose": "Keep observed findings",
        "entries": [{"type": "article_view", "article_id": article_id,
                     "statement": "A conditional author view",
                     "evidence_refs": [read["evidence_id"]]}],
    }
    for invalid_ref in ("evidence_fake", article_id, "../another-run"):
        bad = {**payload, "entries": [{**payload["entries"][0],
                                      "evidence_refs": [invalid_ref]}]}
        with pytest.raises(ValueError, match="canonical EvidenceRef"):
            gateway.call("chat_agent_loop", "research.artifact", operation="save",
                         kind="research_note", payload=bad)
    with pytest.raises(ValueError, match="article_id must match"):
        gateway.call("chat_agent_loop", "research.artifact", operation="save",
                     kind="research_note", payload={**payload, "entries": [{
                         **payload["entries"][0], "article_id": "article_wrong",
                     }]})
    with pytest.raises(ValueError, match="saved current-run claims"):
        gateway.call("chat_agent_loop", "research.artifact", operation="save",
                     kind="research_note", payload={**payload, "entries": [{
                         **payload["entries"][0], "claim_ids": ["claim_fake"],
                     }]})
    with pytest.raises(ValueError, match="evidence_refs"):
        gateway.call("chat_agent_loop", "research.artifact", operation="save",
                     kind="research_note", payload={**payload, "entries": [{
                         "type": "article_view", "statement": "Unbound claim",
                     }]})


def test_research_artifact_is_model_visible_and_traceable(research):
    gateway, _ = research
    from policy_impact.runtime.plugin_config import read_plugin_json, read_plugin_skill_modules

    skill = next(item for item in read_plugin_json("skills", "manifest.json")
                 if item["id"] == "general_chat")
    names = {item["name"] for item in read_plugin_toolkit("finance_research.json")}
    assert "research.artifact" in names & set(skill["allowed_tools"])
    assert "article_card" in read_plugin_skill_modules(skill["skill_modules"])
    gateway.call("chat_agent_loop", "research.artifact", operation="list")
    assert gateway.calls[-1].tool_name == "research.artifact"
    assert gateway.calls[-1].status == "ok"


def test_objective_fact_corroboration_rejects_same_article_url(research):
    gateway, root = research
    path = root / "TT" / "articles" / "2026-09-28-note.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "[Source](https://example.org/tt)\n",
        encoding="utf-8",
    )
    gateway.corpus.sync()
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=5)
    card = gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="article_card",
                        payload={"article_id": article_id, "question": "Check the yield assertion",
                                 "depth": "deep", "claims": [{
                                     "kind": "objective_fact", "statement": "Yields fell",
                                     "quote": "real yields fall", "line_start": 3, "line_end": 3,
                                     "evidence_id": read["evidence_id"],
                                 }]})
    card_id = card["artifact_id"]
    claim_id = gateway.call("chat_agent_loop", "research.artifact", operation="get",
                            artifact_id=card_id)["payload"]["claims"][0]["claim_id"]
    same_page = gateway.evidence_ledger.add_reference(
        source_type="public_web_page", provenance="retrieved_page",
        source_ref="https://example.org/tt", source_url="https://example.org/tt",
    )
    def review(evidence_id):
        return gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="review",
                            payload={"card_ids": [card_id], "checks": [{
                                "claim_id": claim_id, "claim_type": "objective_fact",
                                "status": "supported", "evidence_refs": [read["evidence_id"], evidence_id],
                                "reason": "Compared the source assertion",
                                "suggested_revision": "State the fact with both source dates.",
                                "as_of": "2026-10-08",
                            }]})

    with pytest.raises(ValueError, match="separately read"):
        review(same_page.evidence_id)
    other_source = gateway.evidence_ledger.add_reference(
        source_type="official_source", provenance="retrieved_primary_source",
        source_ref="https://example.org/yields", source_url="https://example.org/yields",
        retrieved_at="2026-10-08T00:00:00Z",
    )
    assert review(other_source.evidence_id)["check_count"] == 1


def test_list_skips_corrupt_record_but_get_reports_it(research):
    gateway, _ = research
    root = gateway.artifacts.root
    assert root is not None
    root.mkdir(parents=True)
    bad_id = "card_" + "a" * 24
    (root / f"{bad_id}.json").write_text("{not json", encoding="utf-8")

    assert gateway.call("chat_agent_loop", "research.artifact", operation="list")["artifacts"] == []
    with pytest.raises(ValueError):
        gateway.call("chat_agent_loop", "research.artifact", operation="get", artifact_id=bad_id)


def test_article_study_deep_reads_every_section_and_saves_one_card(research):
    gateway, root = research
    folder = root / "Long" / "articles"
    folder.mkdir(parents=True)
    (folder / "2026-09-30-long.md").write_text(
        "# Long note\n2026-09-30\n" + "\n".join(
            f"Line {index} discusses gold under condition {index}." for index in range(3, 123)
        ) + "\n", encoding="utf-8",
    )
    gateway.corpus.sync()
    article_id = gateway.call("chat_agent_loop", "article.list", author="Long")["articles"][0]["article_id"]
    calls = []
    merge_calls = []

    def complete(messages, schema_name):
        if schema_name == "finance_article_merge.v1":
            content = messages[1]["content"]
            merge_calls.append(content)
            claims = content["claims"]
            return {"entries": [{
                "statement": "The article links the observed gold view to its stated conditions.",
                "claim_ids": [item["claim_id"] for item in claims],
                "evidence_refs": [item["evidence_id"] for item in claims],
                "conditions": "The conditions remain those stated in the article.",
                "time_scope": "2026-09-30",
            }]}
        assert schema_name == "finance_article_study.v1"
        section = messages[1]["content"]
        calls.append((section["line_start"], section["line_end"]))
        line = 3 if section["line_start"] == 1 else section["line_start"]
        return {"claims": [{
            "kind": "author_view", "statement": f"The author discusses gold condition {line}.",
            "quote": f"Line {line} discusses gold under condition {line}.",
            "line_start": line, "line_end": line,
        }]}

    gateway.article_study.complete = complete
    result = gateway.call("chat_agent_loop", "article.study", article_id=article_id,
                          depth="deep", question="What are the gold conditions?")
    assert calls == [(1, 100), (101, 122)]
    assert result["status"] == "studied"
    assert result["claim_count"] == 2
    assert result["research_note_id"].startswith("note_")
    assert len(merge_calls) == 1
    assert gateway.read_refs[0]["line_start"] == 1
    assert gateway.read_refs[-1]["line_end"] == 122
    card = gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=result["card_id"])["payload"]
    assert card["depth"] == "deep" and card["observed_lines"] == card["line_count"] == 122
    note = gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=result["research_note_id"])["payload"]
    assert note["entries"][0]["claim_ids"] == [
        item["claim_id"] for item in gateway.call(
            "chat_agent_loop", "research.artifact", operation="get",
            artifact_id=result["card_id"],
        )["payload"]["claims"]
    ]
    assert note["entries"][0]["evidence_refs"] == [
        item["evidence_id"] for item in card["claims"]
    ]


def test_article_study_deep_keeps_card_when_article_merge_fails(research):
    gateway, _ = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    schemas = []

    def complete(messages, schema_name):
        schemas.append(schema_name)
        if schema_name == "finance_article_merge.v1":
            raise ValueError("merge adapter unavailable")
        return {"claims": [{
            "kind": "author_view",
            "statement": "Gold rises when real yields fall.",
            "quote": "Gold rises when real yields fall.",
            "line_start": 3,
            "line_end": 3,
        }]}

    gateway.article_study.complete = complete
    result = gateway.call("chat_agent_loop", "article.study", article_id=article_id,
                          depth="deep", question="What is the article view?")
    assert result["status"] == "studied"
    assert result["card_id"].startswith("card_")
    assert result["research_note_id"] is None
    assert "Article-level merge was not saved" in result["limitations"]
    assert schemas[-1] == "finance_article_merge.v1"


def test_article_study_skim_is_explicit_and_rejects_fabricated_quote(research):
    gateway, _ = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    gateway.article_study.complete = lambda messages, schema: {"claims": [{
        "kind": "author_view", "statement": "Gold may rise as real yields fall.",
        "quote": "Gold rises when real yields fall.", "line_start": 3, "line_end": 3,
    }]}
    with pytest.raises(ValueError, match="requires start_line"):
        gateway.call("chat_agent_loop", "article.study", article_id=article_id,
                     depth="skim", question="What is the view?")
    result = gateway.call("chat_agent_loop", "article.study", article_id=article_id,
                          depth="skim", question="What is the view?", start_line=3, limit=1)
    assert result["claims"][0]["evidence_id"] in gateway.evidence_ids
    assert gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=result["card_id"])["payload"]["depth"] == "skim"

    fabricated = {"claims": [{
        "kind": "author_view", "statement": "Fabricated statement about gold.",
        "quote": "Gold always rises without conditions.", "line_start": 3, "line_end": 3,
    }]}
    original = deepcopy(fabricated)
    calls = []

    def fabricate(messages, schema_name):
        calls.append(deepcopy(messages))
        _assert_output_contract(messages, StudyBatch)
        return fabricated

    gateway.article_study.complete = fabricate
    with pytest.raises(ValueError, match="verbatim"):
        gateway.call("chat_agent_loop", "article.study", article_id=article_id,
                     depth="skim", question="What is the view?", start_line=3, limit=1)
    assert len(calls) == 3
    assert fabricated == original
    for messages in calls:
        assert messages[1]["content"]["question"] == "What is the view?"
        assert messages[1]["content"]["source_lines"] == "3: Gold rises when real yields fall."
    assert calls[-1][-1]["content"]["validation_errors"][0]["field"] == "semantic_contract"


def test_article_study_can_report_no_relevant_claim_without_fake_card(research):
    gateway, _ = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    gateway.article_study.complete = lambda messages, schema: {
        "claims": [], "limitations": "This passage does not address silver."
    }
    result = gateway.call("chat_agent_loop", "article.study", article_id=article_id,
                          depth="skim", question="What is the silver view?", start_line=3, limit=1)
    assert result["status"] == "no_relevant_claims"
    assert gateway.call("chat_agent_loop", "research.artifact", operation="list")["count"] == 0


def test_synthesis_uses_two_studied_articles_and_rejects_invented_claim(research):
    gateway, _ = research
    cards = []
    for author, quote in (
        ("TT", "Gold rises when real yields fall."),
        ("Other", "Gold may weaken if the dollar rises."),
    ):
        article_id = gateway.call("chat_agent_loop", "article.list", author=author)["articles"][0]["article_id"]
        read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=4)
        saved = _card(gateway, article_id, read, quote, depth="deep")
        cards.append(gateway.call("chat_agent_loop", "research.artifact", operation="get",
                                  artifact_id=saved["artifact_id"]))
    ids = [card["artifact_id"] for card in cards]
    claims = [card["payload"]["claims"][0]["claim_id"] for card in cards]
    seen = []

    def complete(messages, schema_name):
        if schema_name == "finance_relation_review.v1":
            return _retain_relation_review(messages)
        assert schema_name == "finance_article_synthesis.v1"
        seen.append(messages[1]["content"])
        return {"relations": [{
            "type": "complementary", "claim_ids": claims,
            "analysis": "These views describe different conditions affecting gold.",
        }]}

    gateway.synthesis.complete = complete
    result = gateway.call("chat_agent_loop", "research.synthesize",
                          question="Compare gold conditions", card_ids=ids)
    assert result["status"] == "compared" and result["relation_count"] == 1
    assert len(seen[0]["article_views"]) == 2
    assert "content" not in seen[0]["article_views"][0]
    assert gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=result["comparison_id"])["payload"]["relations"][0]["claim_ids"] == claims
    assert gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=result["comparison_id"])["payload"]["relation_audit"][0][
                            "reason"] == "The quoted claims support this bounded cross-article relation."

    gateway.synthesis.complete = lambda messages, schema: _retain_relation_review(messages) if schema == "finance_relation_review.v1" else {"relations": [
        {"type": "agreement", "claim_ids": [claims[0], claims[0]],
         "analysis": "Both claims are from one article and cannot establish a comparison."},
        {"type": "complementary", "claim_ids": claims,
         "analysis": "The two articles describe different conditional gold drivers."},
    ]}
    screened = gateway.call("chat_agent_loop", "research.synthesize",
                            question="Compare gold conditions", card_ids=ids)
    assert screened["status"] == "compared"
    assert screened["relation_count"] == 1
    assert screened["dropped_relation_count"] == 1

    gateway.synthesis.complete = lambda messages, schema: _retain_relation_review(messages) if schema == "finance_relation_review.v1" else {"relations": [{
        "type": "agreement", "claim_ids": [claims[0], claims[0]],
        "analysis": "A single article cannot independently establish agreement.",
    }]}
    screened_empty = gateway.call("chat_agent_loop", "research.synthesize",
                                  question="Compare gold conditions", card_ids=ids)
    assert screened_empty["status"] == "no_supported_relation"
    assert screened_empty["dropped_relation_count"] == 1

    def reject_inference(messages, schema):
        if schema == "finance_relation_review.v1":
            return {"judgments": [{
                "relation_index": 0, "verdict": "reject",
                "reason": "The later quote does not establish the earlier condition occurred.",
            }]}
        return {"relations": [{
            "type": "change_over_time", "claim_ids": claims,
            "analysis": "The later article confirms the earlier conditional gold scenario.",
        }]}

    gateway.synthesis.complete = reject_inference
    rejected = gateway.call("chat_agent_loop", "research.synthesize",
                            question="Compare gold conditions", card_ids=ids)
    assert rejected["status"] == "no_supported_relation"
    assert rejected["rejected_relation_count"] == 1
    assert rejected["relation_audit"][0]["verdict"] == "reject"

    gateway.synthesis.complete = lambda messages, schema: {"relations": [{
        "type": "agreement", "claim_ids": [claims[0], "claim_fake"],
        "analysis": "This is not bound to an actual second source.",
    }]}
    with pytest.raises(ValueError, match="selected articles"):
        gateway.call("chat_agent_loop", "research.synthesize",
                     question="Compare gold conditions", card_ids=ids)

    gateway.synthesis.complete = lambda messages, schema: {
        "relations": [], "limitations": "The two selected passages discuss different questions."
    }
    empty = gateway.call("chat_agent_loop", "research.synthesize",
                         question="Compare unrelated claims", card_ids=ids)
    assert empty["status"] == "no_supported_relation"


def test_typed_verify_records_attribution_and_rejects_unsupplied_evidence(research):
    gateway, _ = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=4)
    saved = _card(gateway, article_id, read, "Gold rises when real yields fall.", depth="deep")
    card = gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=saved["artifact_id"])
    claim = card["payload"]["claims"][0]
    other = gateway.evidence_ledger.add_reference(
        source_type="official_source", provenance="retrieved_primary_source",
        source_ref="https://example.org/independent", source_url="https://example.org/independent",
        excerpt="A separate source says real yields fell.",
    )
    seen = []

    def check(messages, schema_name):
        assert schema_name == "finance_typed_review.v1"
        _assert_output_contract(messages, TypedReviewBatch)
        seen.append(messages[1]["content"])
        return {"checks": [{
            "claim_id": claim["claim_id"], "claim_type": "author_view",
            "status": "viewpoint_faithful", "evidence_refs": [claim["evidence_id"]],
            "conflicts": [], "alternative_explanations": [],
            "suggested_revision": "Write that TT argues gold rises under falling real yields.",
            "as_of": "2026-10-08", "reason": "The cited article line states the conditional view.",
            "follow_up_queries": ["Check whether real yields actually fell"],
        }]}

    gateway.verification.complete = check
    result = gateway.call("chat_agent_loop", "research.verify",
                          card_ids=[card["artifact_id"]], as_of="2026-10-08")
    assert result["check_count"] == 1
    assert result["follow_up_queries"][0]["claim_id"] == claim["claim_id"]
    assert [row["evidence_id"] for row in seen[0]["evidence"]] == [claim["evidence_id"]]
    assert gateway.call("chat_agent_loop", "research.artifact", operation="get",
                        artifact_id=result["review_id"])["payload"]["checks"][0]["status"] == "viewpoint_faithful"

    def invent(messages, schema_name):
        output = check(messages, schema_name)
        output["checks"][0]["evidence_refs"].append(other.evidence_id)
        return output

    gateway.verification.complete = invent
    with pytest.raises(ValueError, match="not supplied"):
        gateway.call("chat_agent_loop", "research.verify",
                     card_ids=[card["artifact_id"]], as_of="2026-10-08")
    assert len(seen) == 4
    assert all(context == seen[0] for context in seen)


def test_typed_review_future_prediction_cannot_be_marked_supported_by_author_only(research):
    gateway, _ = research
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=4)
    card = gateway.call("chat_agent_loop", "research.artifact", operation="save",
                        kind="article_card", payload={
                            "article_id": article_id, "question": "Forecast status?", "depth": "deep",
                            "claims": [{"kind": "prediction", "statement": "Gold may rise later.",
                                        "quote": "Gold rises when real yields fall.",
                                        "line_start": 3, "line_end": 3,
                                        "evidence_id": read["evidence_id"],
                                        "horizon": "2027-01-01"}],
                        })
    claim = gateway.call("chat_agent_loop", "research.artifact", operation="get",
                         artifact_id=card["artifact_id"])["payload"]["claims"][0]
    check = {
        "claim_id": claim["claim_id"], "claim_type": "prediction", "status": "not_due",
        "evidence_refs": [claim["evidence_id"]], "conflicts": [],
        "alternative_explanations": [], "suggested_revision": "Describe this as an unexpired forecast.",
        "as_of": "2026-10-08", "reason": "The stated forecast horizon is in the future.",
    }
    saved = gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="review",
                         payload={"card_ids": [card["artifact_id"]], "checks": [check]})
    assert saved["check_count"] == 1
    with pytest.raises(ValueError, match="forecast horizon has passed"):
        gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="review",
                     payload={"card_ids": [card["artifact_id"]],
                              "checks": [{**check, "status": "supported"}]})
    with pytest.raises(ValueError, match="future forecast horizon"):
        gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="review",
                     payload={"card_ids": [card["artifact_id"]],
                              "checks": [{**check, "as_of": "2027-02-01"}]})

    future_source = gateway.evidence_ledger.add_reference(
        source_type="official_source", provenance="retrieved_primary_source",
        source_ref="https://example.org/future", source_url="https://example.org/future",
        published_at="2027-02-05", retrieved_at="2027-02-05T00:00:00Z",
    )
    due_check = {
        **check, "status": "supported", "as_of": "2027-02-01",
        "evidence_refs": [claim["evidence_id"], future_source.evidence_id],
    }
    with pytest.raises(ValueError, match="available by as_of"):
        gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="review",
                     payload={"card_ids": [card["artifact_id"]], "checks": [due_check]})
    available_source = gateway.evidence_ledger.add_reference(
        source_type="official_source", provenance="retrieved_primary_source",
        source_ref="https://example.org/available", source_url="https://example.org/available",
        published_at="2027-01-31", retrieved_at="2027-02-01T00:00:00Z",
    )
    saved = gateway.call("chat_agent_loop", "research.artifact", operation="save", kind="review",
                         payload={"card_ids": [card["artifact_id"]], "checks": [{
                             **due_check,
                             "evidence_refs": [claim["evidence_id"], available_source.evidence_id],
                         }]})
    assert saved["check_count"] == 1


@pytest.mark.parametrize("horizon,expected", [
    ("October", date(2026, 10, 31)),
    ("10月", date(2026, 10, 31)),
    ("四季度", date(2026, 12, 31)),
    ("2026-Q4", date(2026, 12, 31)),
])
def test_forecast_horizon_resolves_relative_to_article_date(horizon, expected):
    assert _horizon_end(horizon, "2026-09-30") == expected


def test_article_card_recovers_explicit_forecast_time_and_condition(research):
    gateway, root = research
    path = root / "TT" / "articles" / "2026-09-28-note.md"
    path.write_text(
        path.read_text(encoding="utf-8") +
        "正常剧本下10月金银会向上修复。\n", encoding="utf-8",
    )
    gateway.corpus.sync()
    article_id = gateway.call("chat_agent_loop", "article.list", author="TT")["articles"][0]["article_id"]
    read = gateway.call("chat_agent_loop", "article.read", article_id=article_id, limit=5)
    saved = gateway.call(
        "chat_agent_loop", "research.artifact", operation="save", kind="article_card",
        payload={"article_id": article_id, "question": "October outlook", "depth": "deep",
                 "claims": [{"kind": "prediction", "statement": "Gold may repair in October.",
                             "quote": "正常剧本下10月金银会向上修复。", "line_start": 5,
                             "line_end": 5, "evidence_id": read["evidence_id"]}]},
    )
    card = gateway.call(
        "chat_agent_loop", "research.artifact", operation="get",
        artifact_id=saved["artifact_id"],
    )
    claim = card["payload"]["claims"][0]
    assert claim["horizon"] == "2026-10-31"
    assert "正常剧本下" in claim["conditions"]
