from policy_impact.domains.finance.report_reader import ReportReadGateway


def gateway_with_sources():
    gateway = ReportReadGateway("finance_observer", {"saved"})
    gateway.read_sections.add("sources")
    gateway.observations.append({"items": [{"url": "https://example.com/official", "title": "Official"}]})
    return gateway


def test_requested_source_link_cannot_be_replaced_by_title():
    gateway = gateway_with_sources()
    assert gateway.final_guard("给两条解释，并附来源", {"answer": "来源：Official，报告saved"})


def test_requested_source_link_must_come_from_observation():
    gateway = gateway_with_sources()
    assert gateway.final_guard("附上来源链接", {"answer": "[来源](https://example.com/invented)"})
    assert gateway.final_guard("附上来源链接", {"answer": "[来源](https://example.com/official)"}) is None


def test_no_source_requirement_for_unrelated_question():
    gateway = gateway_with_sources()
    gateway.read_sections.add("analysis")
    assert gateway.final_guard("解释刚才的结论", {"answer": "仅是条件假设"}) is None


def test_missing_source_cannot_force_invention():
    gateway = gateway_with_sources()
    gateway.observations.clear()
    assert gateway.final_guard("附来源", {"answer": "保存快照中没有可提供的原文链接"}) is None


def test_instrument_evidence_link_is_valid_without_unrelated_macro_url():
    gateway = gateway_with_sources()
    gateway.observations.append({"items": [{"session_evidence": {"evidence_urls": ["https://example.com/spot-feed"]}}]})
    assert gateway.final_guard("列出黄金来源链接", {"answer": "[现货参考价](https://example.com/spot-feed)"}) is None


def test_quote_recovers_actual_source_case_and_whitespace():
    from policy_impact.domains.finance.workflow import canonical_source_quote
    source = "As known, the Federal Reserve's program provides\nliquidity to banks.2"
    assert canonical_source_quote("The Federal Reserve's program provides liquidity to banks.", source) == "the Federal Reserve's program provides\nliquidity to banks."
    assert canonical_source_quote("The Federal Reserve's program removes liquidity from banks.", source) is None
    assert canonical_source_quote("", source) is None


def test_quote_does_not_accept_changed_numbers():
    from policy_impact.domains.finance.workflow import canonical_source_quote
    assert canonical_source_quote("gold rose 2%", "Gold rose 1%.") is None


def test_counterevidence_followup_must_read_actual_saved_analysis():
    gateway = gateway_with_sources()
    assert "analysis" in gateway.final_guard("刚才的原因解释最可能被什么事实推翻", {"answer": "我上次认为..."})
    gateway.read_sections.add("analysis")
    assert gateway.final_guard("刚才的原因解释最可能被什么事实推翻", {"answer": "仅是候选假设"}) is None


def test_adapter_description_is_not_provider_self_statement():
    gateway = gateway_with_sources()
    gateway.read_sections.add("instruments")
    assert gateway.final_guard("是什么品种", {"answer": "来源自述为现货"})
    assert gateway.final_guard("是什么品种", {"answer": "适配器分类为现货，原始feed包含AUX元数据"}) is None


def test_sampling_followup_requires_actual_instrument_configuration():
    gateway = gateway_with_sources()
    question = "沿用刚才数据，说明金银的采样口径与来源。"
    assert "instruments" in gateway.final_guard(question, {"answer": "默认六个四小时桶"})
    gateway.read_sections.add("instruments")
    assert gateway.final_guard(question, {"answer": "本快照使用两个十二小时桶"}) is None
