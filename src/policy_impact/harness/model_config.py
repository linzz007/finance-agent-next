"""One process-selected model, with optional named provider configurations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_PROTOCOL = "anthropic"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_STREAM_MAX_SECONDS = 600.0
AUTH_ENV_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
MODEL_ENV_VAR = "ANTHROPIC_MODEL"
BASE_URL_ENV_VAR = "ANTHROPIC_BASE_URL"
PROTOCOL_ENV_VAR = "AGENT_WORKBENCH_MODEL_PROTOCOL"
PROFILE_ENV_VAR = "AGENT_WORKBENCH_MODEL_PROFILE"
MODEL_PROFILES_PATH = Path(__file__).resolve().parents[3] / "config/model_profiles.json"
SUPPORTED_PROTOCOLS = {"anthropic", "openai-compatible", "openai-responses"}
PROVIDER_BY_PROTOCOL = {
    "anthropic": "anthropic-compatible",
    "openai-compatible": "openai-compatible",
    "openai-responses": "openai-responses",
}


class NamedModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, hide_input_in_errors=True)

    display_name: str = Field(min_length=1, max_length=160)
    model_id: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=1000)
    protocol: Literal["openai-compatible", "openai-responses"]
    auth_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    stream: bool
    thinking: bool | None = None
    reasoning_effort: Literal["low", "high", "max"] | None = None

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"https", "http"} or not parts.hostname
                or any(char.isspace() for char in value)
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("Model endpoint must be an HTTP(S) URL without credentials, query or fragment")
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError("Model endpoint port is invalid")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_effort(self):
        if self.protocol == "openai-responses" and (self.thinking is not None or self.reasoning_effort is not None):
            raise ValueError("Responses profiles do not support DeepSeek thinking settings")
        if self.reasoning_effort is not None and self.thinking is not True:
            raise ValueError("Reasoning effort requires thinking enabled")
        return self


class ModelProfileRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal["finance.model_profiles.v1"]
    profiles: dict[str, NamedModelProfile] = Field(min_length=1)


def named_model_profiles() -> dict[str, NamedModelProfile]:
    try:
        payload = json.loads(MODEL_PROFILES_PATH.read_text(encoding="utf-8"))
        return ModelProfileRegistry.model_validate(payload).profiles
    except (OSError, ValueError, ValidationError):
        raise ValueError("Invalid model profile registry; check config/model_profiles.json") from None


def effective_model_config() -> dict[str, Any]:
    """Resolve and validate the single effective model configuration."""

    profile_id = os.getenv(PROFILE_ENV_VAR, "").strip()
    profile = None
    if profile_id:
        profile = named_model_profiles().get(profile_id)
        if profile is None:
            raise ValueError("Unknown model profile; choose a name from config/model_profiles.json")
    auth_names = (profile.auth_env,) if profile else AUTH_ENV_VARS
    auth_var = next((name for name in auth_names if os.getenv(name, "").strip()), "")
    model_id = profile.model_id if profile else (os.getenv(MODEL_ENV_VAR, DEFAULT_MODEL_ID) or DEFAULT_MODEL_ID).strip()
    base_url = profile.base_url if profile else (os.getenv(BASE_URL_ENV_VAR, DEFAULT_BASE_URL) or DEFAULT_BASE_URL).strip().rstrip("/")
    protocol = profile.protocol if profile else (os.getenv(PROTOCOL_ENV_VAR, DEFAULT_PROTOCOL) or DEFAULT_PROTOCOL).strip().lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(
            f"{PROTOCOL_ENV_VAR} must be one of: {', '.join(sorted(SUPPORTED_PROTOCOLS))}"
        )
    max_tokens = int(os.getenv("AGENT_WORKBENCH_MODEL_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)))
    temperature = float(os.getenv("AGENT_WORKBENCH_MODEL_TEMPERATURE", str(DEFAULT_TEMPERATURE)))
    timeout_seconds = float(os.getenv("AGENT_WORKBENCH_MODEL_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
    max_attempts = int(os.getenv("AGENT_WORKBENCH_MODEL_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)))
    stream_value = str(profile.stream).lower() if profile else os.getenv("AGENT_WORKBENCH_MODEL_STREAM", "false").strip().lower()
    if stream_value not in {"true", "false", "1", "0"}:
        raise ValueError("AGENT_WORKBENCH_MODEL_STREAM must be true, false, 1 or 0")
    stream = stream_value in {"true", "1"}
    if stream and protocol not in {"openai-compatible", "openai-responses"}:
        raise ValueError("AGENT_WORKBENCH_MODEL_STREAM requires openai-compatible or openai-responses protocol")
    stream_max_seconds = float(os.getenv(
        "AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS", str(DEFAULT_STREAM_MAX_SECONDS),
    ))
    if not 1 <= stream_max_seconds <= 900:
        raise ValueError("AGENT_WORKBENCH_MODEL_STREAM_MAX_SECONDS must be between 1 and 900")
    if not 256 <= max_tokens <= 32768:
        raise ValueError("AGENT_WORKBENCH_MODEL_MAX_TOKENS must be between 256 and 32768")
    if not 0 <= temperature <= 2:
        raise ValueError("AGENT_WORKBENCH_MODEL_TEMPERATURE must be between 0 and 2")
    if not 1 <= timeout_seconds <= 180:
        raise ValueError("AGENT_WORKBENCH_MODEL_TIMEOUT must be between 1 and 180 seconds")
    if not 1 <= max_attempts <= 5:
        raise ValueError("AGENT_WORKBENCH_MODEL_ATTEMPTS must be between 1 and 5")
    return {
        "model_id": model_id,
        "profile_id": profile_id or None,
        "display_name": profile.display_name if profile else model_id,
        "base_url": base_url,
        "protocol": protocol,
        "provider": PROVIDER_BY_PROTOCOL[protocol],
        "api_key": os.getenv(auth_var, "") if auth_var else "",
        "auth_env": auth_var or auth_names[0],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout_seconds": timeout_seconds,
        "max_attempts": max_attempts,
        "stream": stream,
        "stream_max_seconds": stream_max_seconds,
        "thinking": profile.thinking if profile else False if protocol == "anthropic" else None,
        "reasoning_effort": profile.reasoning_effort if profile else None,
    }


def default_model_config() -> dict[str, Any]:
    """Return the only model row this Workbench should expose."""

    effective = effective_model_config()
    return {
        "model_id": effective["model_id"],
        "display_name": effective["display_name"],
        "provider": effective["provider"],
        "model_name": effective["model_id"],
        "endpoint": effective["base_url"],
        "temperature": effective["temperature"],
        "max_tokens": effective["max_tokens"],
        "is_default": True,
        "metadata": {
            "fixed": True,
            "auth_env": effective["auth_env"],
            "profile_id": effective["profile_id"],
            "base_url_env": BASE_URL_ENV_VAR,
            "model_env": MODEL_ENV_VAR,
            "protocol": effective["protocol"],
            "protocol_env": PROTOCOL_ENV_VAR,
            "stream": effective["stream"],
            "stream_max_seconds": effective["stream_max_seconds"],
            "thinking": effective["thinking"],
            "reasoning_effort": effective["reasoning_effort"],
            "timeout_seconds": effective["timeout_seconds"],
            "max_attempts": effective["max_attempts"],
        },
    }


def configured_model_id() -> str:
    """Return the model ID selected by the current local environment."""

    return str(effective_model_config()["model_id"])


def model_runtime_status() -> dict[str, Any]:
    """Describe model availability without exposing secrets."""

    effective = effective_model_config()
    auth_var = effective["auth_env"] if effective["api_key"] else ""
    configured_model = effective["model_id"]
    base_url = effective["base_url"]
    missing = []
    if not auth_var:
        missing.append(effective["auth_env"])
    return {
        "model_id": configured_model,
        "profile_id": effective["profile_id"],
        "display_name": effective["display_name"],
        "provider": effective["provider"],
        "protocol": effective["protocol"],
        "configured_model": configured_model,
        "base_url": base_url,
        "auth_configured": bool(auth_var),
        "auth_env": effective["auth_env"],
        "available": not missing,
        "missing": missing,
        "temperature": effective["temperature"],
        "max_tokens": effective["max_tokens"],
        "timeout_seconds": effective["timeout_seconds"],
        "max_attempts": effective["max_attempts"],
        "stream": effective["stream"],
        "stream_max_seconds": effective["stream_max_seconds"],
        "thinking": effective["thinking"],
        "reasoning_effort": effective["reasoning_effort"],
    }


def ensure_default_model_available() -> None:
    status = model_runtime_status()
    if status["available"]:
        return
    missing = "、".join(status["missing"])
    raise ValueError(
        "当前没有可用模型，无法执行。"
        f"请在启动服务前配置 {missing}，并检查模型协议与地址配置。"
    )
