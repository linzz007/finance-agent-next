from policy_impact.skill_executors.recent_news_report import _analysis_excerpt


def test_model_evidence_slice_never_stitches_disjoint_release_lines():
    original = "Fixed hooks losing context. " + "Other release information. " * 100 + "Added a subagent flag."
    excerpt = _analysis_excerpt({"summary": "Fixed hooks losing context. Added a subagent flag.",
                                 "original_summary": original, "source_type": "rss_atom", "matched_query_topics": ["subagent"]})
    assert excerpt in original
    assert len(excerpt) <= 6000


def test_model_paper_evidence_is_bounded_original_text():
    original = "Method and limitations. " * 1000
    excerpt = _analysis_excerpt({"original_summary": original, "source_type": "research_paper"})
    assert original.startswith(excerpt)
    assert len(excerpt) == 6000
