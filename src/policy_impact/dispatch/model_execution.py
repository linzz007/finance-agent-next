"""Single-request async transport, reusing the financial gateway's wire contracts."""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import re
import sqlite3
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from policy_impact.harness.context_manifest import estimate_tokens
from policy_impact.harness.model_config import NamedModelProfile, named_model_profiles
from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter
from policy_impact.harness.responses_model_gateway import ResponsesModelAdapter
from policy_impact.runtime.actions import AgentAction, _validate_json_value

from .contracts import DispatchError
from .diagnostics import public_output_normalization, public_validation_errors
from .diagnostics import safe_text as _safe_text
from .finance_actions import (
    extract_public_action_text,
    normalize_json_string_syntax,
    parse_root_action,
)


def _is_dsml_action(content: str) -> bool:
    return re.match(
        r"\s*<(?:\|\||\uff5c\uff5c)DSML(?:\|\||\uff5c\uff5c)\s+calls>",
        content,
    ) is not None


def _is_current_dsml_action(content: str) -> bool:
    return re.match(
        r"\s*<(?:\|\||\uff5c\uff5c)DSML(?:\|\||\uff5c\uff5c)\s+calls>\s*"
        r"<(?:\|\||\uff5c\uff5c)DSML(?:\|\||\uff5c\uff5c)\s+invoke\s+name=\"tool\.call\">\s*"
        r"<(?:\|\||\uff5c\uff5c)DSML(?:\|\||\uff5c\uff5c)\s+parameter\s+name=\"name\"\s+",
        content,
    ) is not None


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    max_tokens: int = Field(default=32768, ge=256, le=32768)
    temperature: float = Field(default=0.0, ge=0, le=2)
    context_tokens: int = Field(default=1_000_000, gt=0, le=1_000_000)
    input_tokens: int = Field(default=1_000_000, gt=0, le=1_000_000)
    safety_tokens: int = Field(default=4096, ge=0)
    connect_seconds: float = Field(default=90.0, gt=0, le=180)
    read_idle_seconds: float = Field(default=90.0, gt=0, le=180)
    call_seconds: float = Field(default=600.0, ge=1, le=900)

    def input_limit(self):
        return max(
            0, min(self.input_tokens, self.context_tokens - self.max_tokens) - self.safety_tokens
        )


@dataclass(frozen=True)
class ModelRequest:
    call_id: str
    model_snapshot: dict
    messages: list[dict]
    schema_name: str
    settings: ModelSettings
    remaining_seconds: float


@dataclass(frozen=True)
class ModelEvent:
    call_id: str
    seq: int
    kind: Literal["started", "text_delta", "usage", "action_candidate", "completed", "failed"]
    data: dict = field(default_factory=dict)


def profiles_from_registry(profile_ids: list[str], *, settings: ModelSettings | None = None):
    """Explicit metadata-only selection; never resolve credentials or change global env."""
    registry = named_model_profiles()
    if (
        not profile_ids
        or len(set(profile_ids)) != len(profile_ids)
        or set(profile_ids) - registry.keys()
    ):
        raise DispatchError("invalid_arguments", "choose registered model profiles explicitly")
    parameters = (settings or ModelSettings()).model_dump(mode="json")
    return {
        profile_id: {
            **registry[profile_id].model_dump(exclude={"display_name"}, mode="json"),
            "parameters": dict(parameters),
        }
        for profile_id in profile_ids
    }


def _is_quota(problem):
    if not isinstance(problem, dict):
        return False
    code = problem.get("code") or problem.get("type")
    return isinstance(code, str) and code in {
        "insufficient_quota",
        "insufficient_user_quota",
        "insufficient_balance",
    }


def _usage(value):
    if not isinstance(value, dict):
        return {}
    # Do not serialize arbitrary provider metadata into the billing ledger.
    keys = {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens"}
    return {k: v for k, v in value.items() if k in keys and type(v) is int and v >= 0}


def _public_id(value, secret):
    return _safe_text(value or "", secret)[:160]


class _SSEBuffer:
    """Bounded framing only; shared gateway parsers validate the terminal response."""

    def __init__(self, responses):
        self.responses = responses
        self.raw = bytearray()
        self.pending = b""
        self.data = []
        self.event_size = 0
        self.first = True

    def feed(self, chunk):
        self.raw.extend(chunk)
        if len(self.raw) > 8 * 1024 * 1024:
            raise ModelInvocationError("stream transport error: response size limit exceeded")
        self.pending += chunk
        start = 0
        for newline in re.finditer(rb"\r\n|\r|\n", self.pending):
            if newline.group() == b"\r" and newline.end() == len(self.pending):
                break
            line = self.pending[start : newline.start()].decode("utf-8")
            start = newline.end()
            self.event_size += len(line.encode())
            if self.event_size > 1024 * 1024:
                raise ModelInvocationError("stream transport error: response size limit exceeded")
            if self.first:
                line = line.removeprefix("\ufeff")
                self.first = False
            if line:
                key, separator, value = line.partition(":")
                if separator and key == "data":
                    self.data.append(value.removeprefix(" "))
            else:
                self.event_size = 0
                data, self.data = "\n".join(self.data), []
                if data:
                    if not self.responses and data == "[DONE]":
                        return True
                    event = json.loads(data)
                    if not isinstance(event, dict):
                        raise ModelInvocationError("stream transport error: invalid SSE event")
                    if not self.responses:
                        if event.get("error"):
                            if _is_quota(event["error"]):
                                raise ModelInvocationError("HTTP 402: quota error")
                            raise ModelInvocationError("provider response rejected: error event")
                        choices = event.get("choices") or []
                        if not isinstance(choices, list):
                            raise ModelInvocationError("stream transport error: invalid choices")
                        for choice in choices:
                            delta = choice.get("delta") if isinstance(choice, dict) else None
                            if isinstance(delta, dict) and (
                                delta.get("tool_calls") or delta.get("function_call")
                            ):
                                raise ModelInvocationError(
                                    "provider response rejected: native tools are unsupported"
                                )
                    if self.responses and event.get("type") in {
                        "response.completed",
                        "response.incomplete",
                        "response.failed",
                        "error",
                    }:
                        return True
        self.pending = self.pending[start:]
        if len(self.pending) > 1024 * 1024:
            raise ModelInvocationError("stream transport error: response size limit exceeded")
        return False


class GatewayModelAdapter:
    """One physical request. No retries, native tools, redirects or profile fallback.

    httpx is imported only when explicitly constructing this optional adapter.
    Public text is buffered until the existing gateway verifies the full terminal.
    """

    def __init__(
        self, *, credential_resolver: Callable[[str], str] | None = None, http_transport=None
    ):
        import httpx

        self.httpx = httpx
        self.credential_resolver = credential_resolver or (lambda name: os.getenv(name, ""))
        self.http_transport = http_transport
        self._active = {}
        self._used = OrderedDict()
        self._cancel_requested = set()

    @staticmethod
    def settings(snapshot):
        try:
            return ModelSettings.model_validate(snapshot.get("parameters", {}))
        except (ValidationError, TypeError):
            raise DispatchError("contract_error", "invalid model parameter snapshot") from None

    @staticmethod
    def profile(snapshot):
        try:
            return NamedModelProfile.model_validate(
                {
                    "display_name": snapshot["profile"],
                    **{k: snapshot[k] for k in ("model_id", "base_url", "protocol", "auth_env")},
                    "stream": snapshot.get("stream", False),
                    "thinking": snapshot.get("thinking"),
                    "reasoning_effort": snapshot.get("reasoning_effort"),
                }
            )
        except (KeyError, TypeError, ValidationError):
            raise DispatchError("contract_error", "invalid model profile snapshot") from None

    async def cancel(self, call_id):
        task = self._active.get(call_id)
        if task is None or task.done():
            return "already_finished"
        self._cancel_requested.add(call_id)
        task.cancel()
        return "abort_requested"

    async def _request(self, codec, system, wire, settings):
        httpx = self.httpx
        timeout = httpx.Timeout(settings.read_idle_seconds, connect=settings.connect_seconds)
        async with (
            httpx.AsyncClient(
                transport=self.http_transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream(
                "POST",
                codec.base_url + codec.endpoint_path,
                json=codec._build_payload(system, wire),
                headers={
                    "Authorization": "Bearer " + codec.api_key,
                    "Accept": "text/event-stream" if codec.stream else "application/json",
                },
            ) as response,
        ):
            request_id = response.headers.get("x-request-id", "")
            if not 200 <= response.status_code < 300:
                # Read only a bounded diagnostic, retaining no provider message/body.
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        break
                problem = {}
                if len(raw) <= 65536:
                    try:
                        problem = json.loads(raw)
                        problem = problem.get("error", {}) if isinstance(problem, dict) else {}
                    except (ValueError, UnicodeError):
                        pass
                return {
                    "http_status": response.status_code,
                    "quota": _is_quota(problem) or response.status_code == 402,
                    "request_id": request_id,
                }
            started = time.perf_counter()
            raw = bytearray()
            buffer = _SSEBuffer(isinstance(codec, ResponsesModelAdapter)) if codec.stream else None
            if (
                codec.stream
                and response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "text/event-stream"
            ):
                raise ModelInvocationError("stream transport error: expected text/event-stream")
            async for chunk in response.aiter_bytes():
                if buffer is not None:
                    if buffer.feed(chunk):
                        break
                else:
                    raw.extend(chunk)
                    if len(raw) > 8 * 1024 * 1024:
                        raise ModelInvocationError(
                            "stream transport error: response size limit exceeded"
                        )
            transport = {
                "stream": codec.stream,
                "api_style": codec.api_style,
                "stream_event_count": 0,
                "stream_content_chars": 0,
                "time_to_first_token_ms": None,
            }
            if buffer is not None:
                data = codec._read_stream(io.BytesIO(buffer.raw), transport, started)
                # Buffered parsing cannot measure time to first token honestly.
                transport.pop("time_to_first_token_ms", None)
            else:
                data = json.loads(raw)
            if not isinstance(codec, ResponsesModelAdapter) and isinstance(data, dict):
                for choice in data.get("choices") or []:
                    message = choice.get("message") if isinstance(choice, dict) else None
                    if isinstance(message, dict) and (
                        message.get("tool_calls") or message.get("function_call")
                    ):
                        raise ModelInvocationError(
                            "provider response rejected: native tools are unsupported"
                        )
                    if isinstance(message, dict) and isinstance(message.get("content"), list):
                        public = []
                        for part in message["content"]:
                            if not isinstance(part, dict):
                                raise ModelInvocationError(
                                    "provider response rejected: invalid content part"
                                )
                            if part.get("type") in {"reasoning", "thinking", "redacted_thinking"}:
                                continue
                            if part.get("type") not in {"text", "output_text"} or not isinstance(
                                part.get("text"), str
                            ):
                                raise ModelInvocationError(
                                    "provider response rejected: unsupported content part"
                                )
                            public.append(part["text"])
                        message["content"] = "".join(public)
            normalized = codec._normalize_response(data, transport, started)
            return {"response": normalized, "request_id": request_id}

    async def request(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        deadline = time.monotonic() + request.remaining_seconds
        settings = self.settings(request.model_snapshot)
        if (
            settings != request.settings
            or not math.isfinite(request.remaining_seconds)
            or request.remaining_seconds <= 0
        ):
            raise DispatchError("contract_error", "invalid model execution envelope")
        profile = self.profile(request.model_snapshot)
        _validate_json_value(request.messages)
        if not request.messages or any(
            not isinstance(m, dict)
            or set(m) != {"role", "content"}
            or not isinstance(m["role"], str)
            or m["role"] not in {"system", "user", "assistant"}
            for m in request.messages
        ):
            raise DispatchError("contract_error", "invalid public model messages")
        if estimate_tokens(request.messages) > settings.input_limit():
            raise DispatchError("contract_error", "context_budget_exceeded")
        try:
            secret = self.credential_resolver(profile.auth_env)
        except (sqlite3.Error, OSError):
            raise
        except Exception:  # noqa: BLE001 - a credential extension must not leak its exception text
            raise DispatchError("provider_error", "credential resolver failed") from None
        if (
            not isinstance(secret, str)
            or not secret
            or not secret.isascii()
            or any(char.isspace() or not char.isprintable() for char in secret)
        ):
            raise DispatchError("provider_error", "configured model credential unavailable")
        if request.call_id in self._used or request.call_id in self._active:
            raise DispatchError("idempotency_conflict", "physical call ID already used")
        self._used[request.call_id] = None
        if len(self._used) > 8192:
            self._used.popitem(last=False)
        codec_type = (
            ResponsesModelAdapter
            if profile.protocol == "openai-responses"
            else OpenAICompatibleModelAdapter
        )
        codec = codec_type(
            model_id=profile.model_id,
            base_url=profile.base_url,
            api_key=secret,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            timeout_seconds=settings.read_idle_seconds,
            max_attempts=1,
            stream=profile.stream,
            stream_max_seconds=settings.call_seconds,
            thinking=profile.thinking,
            reasoning_effort=profile.reasoning_effort,
            profile_id=profile.display_name,
        )
        system, wire = codec._split_messages(request.messages)
        seq = 1
        # The consumer reserves/starts the ledger before requesting the next event.
        yield ModelEvent(request.call_id, seq, "started")
        task = asyncio.create_task(self._request(codec, system, wire, settings))
        self._active[request.call_id] = task
        received = False
        usage, response_id, request_id = {}, "", ""
        normalization = {}
        try:
            try:
                result = await asyncio.wait_for(
                    task, min(settings.call_seconds, max(0, deadline - time.monotonic()))
                )
                request_id = _public_id(result.get("request_id"), secret)
                status = result.get("http_status")
                if status is not None:
                    code = "quota_error" if result["quota"] else "provider_error"
                    yield ModelEvent(
                        request.call_id,
                        seq + 1,
                        "failed",
                        {
                            "code": code,
                            "retryable": False,
                            "physical_status": "failed",
                            "http_status": status,
                            "provider_request_id": request_id,
                        },
                    )
                    return
                data = result["response"]
                received = True
                usage = _usage(data.get("usage"))
                response_id = _public_id(data.get("id"), secret)
                if data.get("stop_reason") != "stop":
                    raise DispatchError("contract_error", "output_truncated")
                text = _safe_text(codec._response_text(data), secret)
                seq += 1
                yield ModelEvent(request.call_id, seq, "text_delta", {"text": text})
                seq += 1
                yield ModelEvent(request.call_id, seq, "usage", {"usage": usage})
                # Parse one bounded public action. Any trailing provider/model text
                # stays in the raw audit artifact and can never become an observation.
                parse_text = text
                is_dsml_action = request.schema_name == "agent_action.v1" and _is_dsml_action(text)
                is_current_dsml_action = is_dsml_action and _is_current_dsml_action(text)
                if is_current_dsml_action:
                    normalization = {
                        "public_output_normalization": {"dsml_action": "deepseek_text_tool"},
                    }
                if request.schema_name == "agent_action.v1" and not is_dsml_action:
                    try:
                        parse_text, boundary = extract_public_action_text(text)
                    except ValueError:
                        # Keep the bounded normalization fact visible even when
                        # the candidate later fails JSON or Schema validation.
                        _, syntax = normalize_json_string_syntax(text)
                        detail = public_output_normalization(syntax)
                        if detail:
                            normalization = {"public_output_normalization": detail}
                        raise
                    detail = public_output_normalization(boundary)
                    if detail:
                        normalization = {"public_output_normalization": detail}
                payload = codec._parse_action_payload(parse_text)
                payload = codec._normalize_schema_envelope(payload, request.schema_name)
                codec._validate_schema(payload, request.schema_name)
                if request.schema_name == "agent_action.v1":
                    action, warnings = parse_root_action(payload)
                    # Keep the rejected delta visible to the Root, which owns partial delivery.
                    if not warnings:
                        payload = action.model_dump(mode="json")
                elif request.schema_name in {
                    "general_chat_agent_loop.v1",
                    "finance.research_result.v1",
                }:
                    payload = (
                        TypeAdapter(AgentAction).validate_python(payload).model_dump(mode="json")
                    )
                seq += 1
                yield ModelEvent(request.call_id, seq, "action_candidate", {"payload": payload})
                seq += 1
                yield ModelEvent(
                    request.call_id,
                    seq,
                    "completed",
                    {
                        "usage": usage,
                        "response_id": response_id,
                        "provider_request_id": request_id,
                        **normalization,
                    },
                )
            except asyncio.CancelledError:
                if request.call_id not in self._cancel_requested:
                    raise
                yield ModelEvent(
                    request.call_id,
                    seq + 1,
                    "failed",
                    {
                        "code": "cancelled",
                        "retryable": False,
                        "physical_status": "unknown",
                        **normalization,
                    },
                )
            except (
                DispatchError,
                ModelInvocationError,
                ValueError,
                OSError,
                self.httpx.HTTPError,
                TimeoutError,
            ) as exc:
                if isinstance(exc, DispatchError):
                    code = exc.code
                elif isinstance(exc, (TimeoutError, self.httpx.TimeoutException)):
                    code = "timeout"
                elif isinstance(exc, self.httpx.TransportError):
                    code = "transport_error"
                elif isinstance(exc, ModelInvocationError):
                    status = re.match(r"^HTTP (\d{3})(?::|\b)", str(exc))
                    if status:
                        code = "quota_error" if int(status[1]) == 402 else "provider_error"
                    else:
                        code = (
                            "provider_error"
                            if str(exc).startswith("provider")
                            else "transport_error"
                        )
                else:
                    code = "contract_error"
                diagnostic = {}
                if isinstance(exc, ValidationError):
                    diagnostic = {
                        "validation_stage": (
                            "final.output" if request.schema_name == "finance.research_result.v1" else "action"
                        ),
                        "validation_errors": public_validation_errors(
                            exc.errors(include_input=False, include_context=False), secret=secret,
                        ),
                    }
                yield ModelEvent(
                    request.call_id,
                    seq + 1,
                    "failed",
                    {
                        "code": code,
                        "retryable": code == "transport_error",
                        "physical_status": "succeeded"
                        if received
                        else "failed"
                        if code in {"quota_error", "provider_error"}
                        else "unknown",
                        "usage": usage,
                        "response_id": response_id,
                        "provider_request_id": request_id,
                        **diagnostic,
                        **normalization,
                    },
                )
        finally:
            self._active.pop(request.call_id, None)
            self._cancel_requested.discard(request.call_id)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
