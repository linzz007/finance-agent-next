"""Named provider selection and transport contracts, with fake credentials only."""

import json

import pytest
from pydantic import ValidationError

from policy_impact.harness import model_config as config
from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter
from run_finance import configure_environment


@pytest.fixture(autouse=True)
def clean_model_environment(monkeypatch):
    for key in (
        *config.AUTH_ENV_VARS, "DEEPSEEK_API_KEY", config.MODEL_ENV_VAR,
        config.BASE_URL_ENV_VAR, config.PROTOCOL_ENV_VAR, config.PROFILE_ENV_VAR,
        "AGENT_WORKBENCH_MODEL_MAX_TOKENS", "AGENT_WORKBENCH_MODEL_TEMPERATURE",
        "AGENT_WORKBENCH_MODEL_TIMEOUT", "AGENT_WORKBENCH_MODEL_ATTEMPTS",
        "AGENT_WORKBENCH_MODEL_STREAM", "AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    # Local env loading writes these directly; track them for fixture rollback.
    monkeypatch.setenv(config.PROFILE_ENV_VAR, "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")


def test_no_profile_preserves_legacy_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "legacy-fixture")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://legacy.invalid/v1/")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_PROTOCOL", "openai-compatible")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "legacy-fixture-key")
    selected = config.effective_model_config()
    assert selected["profile_id"] is None
    assert selected["model_id"] == "legacy-fixture"
    assert selected["base_url"] == "https://legacy.invalid/v1"
    assert selected["api_key"] == "legacy-fixture-key"
    assert selected["thinking"] is None
    assert selected["stream"] is False
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_PROTOCOL", "anthropic")
    assert config.effective_model_config()["thinking"] is False


@pytest.mark.parametrize("profile,model,host,auth,thinking", [
    ("gpt", "gpt-6.1-sol", "https://www.cctq.ai/v1", "ANTHROPIC_AUTH_TOKEN", None),
    ("deepseek", "deepseek-flash", "https://api.deepseek.com", "DEEPSEEK_API_KEY", True),
])
def test_profiles_own_provider_selection(monkeypatch, profile, model, host, auth, thinking):
    monkeypatch.setenv("ANTHROPIC_MODEL", "wrong-legacy-model")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://wrong.invalid")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_PROTOCOL", "anthropic")
    monkeypatch.setenv("AGENT_WORKBENCH_MODEL_STREAM", "false")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "gpt-fixture-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-fixture-key")
    monkeypatch.setenv(config.PROFILE_ENV_VAR, profile)
    selected = config.effective_model_config()
    assert selected["profile_id"] == profile
    assert selected["model_id"] == model
    assert selected["base_url"] == host
    assert selected["protocol"] == "openai-compatible"
    assert selected["auth_env"] == auth
    assert selected["api_key"] == ("gpt-fixture-key" if profile == "gpt" else "deepseek-fixture-key")
    assert selected["stream"] is True
    assert selected["thinking"] is thinking
    adapter = OpenAICompatibleModelAdapter.from_env()
    assert adapter.model_id == model
    assert adapter.base_url == host
    assert adapter.profile_id == profile
    public = json.dumps([config.model_runtime_status(), config.default_model_config()])
    assert "fixture-key" not in public
    assert config.model_runtime_status()["available"] is True


@pytest.mark.parametrize("profile,other_key,required", [
    ("deepseek", "ANTHROPIC_AUTH_TOKEN", "DEEPSEEK_API_KEY"),
    ("gpt", "DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
])
def test_missing_credential_never_falls_back_to_other_provider(monkeypatch, profile, other_key, required):
    monkeypatch.setenv(config.PROFILE_ENV_VAR, profile)
    monkeypatch.setenv(other_key, "other-provider-fixture-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "legacy-fallback-fixture-key")
    monkeypatch.setenv(required, "   ")
    assert config.effective_model_config()["api_key"] == ""
    status = config.model_runtime_status()
    assert status["available"] is False
    assert status["missing"] == [required]
    with pytest.raises(ValueError, match=required):
        config.ensure_default_model_available()


def test_unknown_profile_has_no_silent_fallback(monkeypatch):
    monkeypatch.setenv(config.PROFILE_ENV_VAR, "unknown-fixture")
    with pytest.raises(ValueError, match="Unknown model profile"):
        config.effective_model_config()


@pytest.mark.parametrize("field,value", [
    ("base_url", "file:///private"), ("base_url", "https://user:secret@fixture.invalid"),
    ("base_url", "https://@fixture.invalid"), ("base_url", "https://fixture.invalid?q=secret"),
    ("base_url", "https://fixture.invalid/#fragment"), ("base_url", "https://bad host.invalid"),
    ("base_url", "https://fixture.invalid:invalid"), ("base_url", "https://fixture.invalid:70000"),
    ("stream", "true"), ("stream", 1), ("thinking", "false"),
    ("auth_env", "sk-literal-key-not-variable"), ("api_key", "secret-fixture"),
    ("reasoning_effort", "medium"), ("model_id", ""), ("protocol", "unsupported"),
])
def test_strict_profile_contract_rejects_invalid_values(field, value):
    payload = config.named_model_profiles()["gpt"].model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        config.NamedModelProfile.model_validate(payload)


def test_reasoning_effort_requires_enabled_thinking():
    payload = config.named_model_profiles()["deepseek"].model_dump()
    payload["thinking"] = False
    with pytest.raises(ValidationError, match="requires thinking"):
        config.NamedModelProfile.model_validate(payload)


@pytest.mark.parametrize("content", [
    "not JSON secret-fixture", '{"schema_version":"unknown","profiles":{}}',
    '{"schema_version":"finance.model_profiles.v1","profiles":{},"api_key":"secret-fixture"}',
])
def test_registry_errors_do_not_echo_raw_config(tmp_path, monkeypatch, content):
    path = tmp_path / "model_profiles.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(config, "MODEL_PROFILES_PATH", path)
    with pytest.raises(ValueError, match="Invalid model profile registry") as error:
        config.named_model_profiles()
    assert "secret-fixture" not in str(error.value)


def test_cli_profile_applied_after_local_env_without_mutating_file(tmp_path, monkeypatch):
    (tmp_path / "data/finance/articles").mkdir(parents=True)
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", "")
    env = tmp_path / ".env.local"
    original = "AGENT_WORKBENCH_MODEL_PROFILE=gpt\nDEEPSEEK_API_KEY=fixture-only\n"
    env.write_text(original, encoding="utf-8")
    configure_environment(tmp_path, model_profile="deepseek")
    assert config.model_runtime_status()["profile_id"] == "deepseek"
    assert env.read_text(encoding="utf-8") == original


def test_environment_profile_is_used_when_cli_omitted(tmp_path, monkeypatch):
    (tmp_path / "data/finance/articles").mkdir(parents=True)
    monkeypatch.setenv("FINANCE_ARTICLE_ROOT", "")
    monkeypatch.setenv(config.PROFILE_ENV_VAR, "deepseek")
    configure_environment(tmp_path, load_files=False)
    assert config.configured_model_id() == "deepseek-flash"


def test_startup_validates_profile_before_accessing_corpus(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="Unknown model profile"):
        configure_environment(tmp_path, load_files=False, model_profile="unknown-fixture")


@pytest.mark.parametrize("profile", ["gpt", "deepseek"])
def test_provider_payload_metadata_and_hidden_reasoning(monkeypatch, profile):
    from tests.test_openai_streaming import StreamResponse, chunk, event

    monkeypatch.setenv(config.PROFILE_ENV_VAR, profile)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "gpt-fixture-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-fixture-key")
    captured = []
    content = '{"type":"final","output":{"answer":"Public answer"}}'

    def fake_request(req, timeout):
        captured.append((req.full_url, json.loads(req.data), req.get_header("Authorization")))
        hidden = {"choices": [{"index": 0, "delta": {"reasoning_content": "hidden-fixture-reasoning"}}]}
        return StreamResponse(event(hidden) + event(chunk(content, finish="stop")) + event("[DONE]"))

    monkeypatch.setattr("policy_impact.harness.openai_model_gateway.request.urlopen", fake_request)
    response = OpenAICompatibleModelAdapter.from_env().complete(
        [{"role": "user", "content": "Fixture question"}], "general_chat_agent_loop.v1")
    url, payload, authorization = captured[0]
    if profile == "deepseek":
        assert url == "https://api.deepseek.com/chat/completions"
        assert authorization == "Bearer deepseek-fixture-key"
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["reasoning_effort"] == "high"
        assert "temperature" not in payload
    else:
        assert url == "https://www.cctq.ai/v1/chat/completions"
        assert authorization == "Bearer gpt-fixture-key"
        assert "thinking" not in payload
        assert "reasoning_effort" not in payload
        assert payload["temperature"] == 0.0
    assert "tools" not in payload
    assert response.payload["output"]["answer"] == "Public answer"
    assert response.metadata["model_profile"] == profile
    assert response.metadata["requested_model_id"] == config.configured_model_id()
    assert response.metadata["returned_model_id"] == "test-model"
    assert response.metadata["stream_complete"] is True
    exported = json.dumps({"payload": response.payload, "metadata": response.metadata})
    assert "hidden-fixture-reasoning" not in exported
    assert "fixture-key" not in exported
