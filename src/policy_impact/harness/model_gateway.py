"""Model gateway contracts and the production Anthropic-compatible adapter."""

from __future__ import annotations

import json
import http.client
import math
import re
import time
from copy import copy
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib import error, request

from policy_impact.harness.model_config import effective_model_config
from policy_impact.harness.state import utc_now_iso

_SUBAGENT_SCHEMA_OUTPUT_KEYS = {
    "evidence_collection.v1": ("evidence",),
    "claim_set.v1": ("claims",),
    "counterexample_set.v1": ("counterexamples", "notes"),
    "claim_verdict_set.v1": ("verdicts",),
    "published_answer.v1": ("answer", "verified_claim_refs"),
}
_ACTION_TYPES = {"final", "tool_call", "delegate", "request_approval"}


@dataclass
class ModelResponse:
    response_id: str
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelAdapter(Protocol):
    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        ...


class ScriptedModelAdapter:
    """Explicit test double. Production code must not instantiate this adapter."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self._index = 0

    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        self._index += 1
        if self._index <= len(self._payloads):
            payload = self._payloads[self._index - 1]
        else:
            payload = {"type": "final", "output": {}}
        return ModelResponse(response_id=f"scripted_{self._index}", payload=payload)


class ModelInvocationError(RuntimeError):
    """A sanitized model error suitable for API responses and trace records."""

    def __init__(
        self,
        message: str,
        *,
        structured_output: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        # Malformed model contracts may enter the structured repair pass;
        # transport/provider failures must continue to fail normally.
        self.structured_output = structured_output
        self.metadata = dict(metadata or {})


class AnthropicCompatibleModelAdapter:
    """Call an Anthropic-compatible ``/v1/messages`` endpoint with JSON output."""

    provider_name = "anthropic-compatible"

    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        api_key: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        timeout_seconds: float = 45.0,
        max_attempts: int = 2,
    ) -> None:
        if not api_key:
            raise ValueError("当前没有可用模型，无法执行。缺少模型鉴权信息。")
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)

    @classmethod
    def from_env(cls) -> "AnthropicCompatibleModelAdapter":
        config = effective_model_config()
        return cls(
            model_id=config["model_id"],
            base_url=config["base_url"],
            api_key=config["api_key"],
            max_tokens=config["max_tokens"],
            temperature=config["temperature"],
            timeout_seconds=config["timeout_seconds"],
            max_attempts=config["max_attempts"],
        )

    def complete(self, messages: list[dict[str, Any]], schema_name: str) -> ModelResponse:
        from policy_impact.harness.research_budget import CURRENT

        if CURRENT.get() is not None:
            from policy_impact.harness.research_budget import decision_scope
            with decision_scope():
                return self._complete_research(messages, schema_name)
        system, anthropic_messages = self._split_messages(messages)
        original_messages = [dict(item) for item in anthropic_messages]
        last_error = ""
        retry_errors: list[str] = []
        transport_attempts: list[dict[str, Any]] = []
        attempts_used = 0
        last_error_kind = "unknown"
        total_started = time.perf_counter()

        for attempt in range(1, self.max_attempts + 1):
            attempts_used = attempt
            started = time.perf_counter()
            response_text = ""
            try:
                data = self._request(system, anthropic_messages)
                transport_metadata = data.get("transport_metadata") or {}
                if transport_metadata:
                    transport_attempts.append({"attempt": attempt, **transport_metadata})
                if str(data.get("stop_reason") or "") == "max_tokens":
                    raise ValueError("模型输出被 max_tokens 截断")
                response_text = self._response_text(data)
                payload = self._parse_json_payload(response_text)
                payload = self._normalize_schema_envelope(payload, schema_name)
                self._validate_schema(payload, schema_name)
                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                return ModelResponse(
                    response_id=str(data.get("id") or f"anthropic_{int(time.time() * 1000)}"),
                    payload=payload,
                    metadata={
                        "adapter": type(self).__name__,
                        "provider": self.provider_name,
                        "model_id": str(data.get("model") or self.model_id),
                        "schema_name": schema_name,
                        "attempts": attempt,
                        "latency_ms": latency_ms,
                        "total_latency_ms": round((time.perf_counter() - total_started) * 1000, 2),
                        "stop_reason": str(data.get("stop_reason") or ""),
                        "usage": data.get("usage") or {},
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                        "timeout_seconds": self.timeout_seconds,
                        "max_attempts": self.max_attempts,
                        "retry_errors": list(retry_errors),
                        **transport_metadata,
                        "transport_attempts": list(transport_attempts),
                    },
                )
            except (ModelInvocationError, ValueError) as exc:
                if isinstance(exc, ModelInvocationError) and exc.metadata.get("transport_metadata"):
                    transport_attempts.append({"attempt": attempt, **exc.metadata["transport_metadata"]})
                last_error = str(exc)
                retry_errors.append(last_error[:240])
                retryable_transport = (
                    isinstance(exc, ModelInvocationError)
                    and self._is_retryable_transport_error(last_error)
                )
                last_error_kind = (
                    "transport" if retryable_transport
                    else "structured" if isinstance(exc, ValueError) else "runtime"
                )
                if attempt >= self.max_attempts:
                    break
                if isinstance(exc, ModelInvocationError):
                    if not retryable_transport:
                        break
                    # No model output exists to repair after a transport error.
                    # Retry the current request without altering its instructions.
                elif "max_tokens" in last_error:
                    # Do not feed a truncated multi-thousand-token fragment back to the model.
                    # Re-run the original request without the truncated fragment.
                    anthropic_messages = [dict(item) for item in original_messages]
                    if schema_name == "general_chat_agent_loop.v1":
                        anthropic_messages[-1]["content"] += (
                            "\n\n上一轮 JSON 被输出上限截断。重新生成完整 JSON action。"
                            "如果正在形成最终回答，保留用户要求的比较、结论和来源引用；"
                            "只压缩重复表述，不要将分析缩成清单或几十字摘要。"
                        )
                    else:
                        anthropic_messages[-1]["content"] += (
                            "\n\n上一轮输出过长。重新独立生成：只返回合法 JSON；"
                            "完整 JSON 不超过 900 个中文字符；每个数组严格 2 项；"
                            "每项不超过 60 个中文字符；不要复述问题或重复段落。"
                        )
                else:
                    anthropic_messages = [
                        *original_messages,
                        *(
                            [{"role": "assistant", "content": response_text}]
                            if response_text
                            else []
                        ),
                        {
                            "role": "user",
                            "content": (
                                "上一轮响应没有满足 JSON 契约。"
                                f"错误：{last_error}。请缩短内容，只返回一个合法 JSON 对象，"
                                "不要使用 Markdown 代码块。最终面向用户的字段不得提及格式错误、"
                                "重试、契约修复或上一轮响应。"
                            ),
                        },
                    ]
                # Unstable compatible endpoints often need a real cooldown
                # after EOF/reset responses. The caller already bounds the
                # total attempts; this backoff keeps retries from becoming a
                # tight reconnect loop.
                time.sleep(min(8.0, 1.0 * (2 ** (attempt - 1))))

        recovered = (
            None if "max_tokens" in last_error
            else self._recover_bounded_payload(response_text, schema_name)
        )
        if recovered is not None:
            return ModelResponse(
                response_id=f"recovered_{int(time.time() * 1000)}",
                payload=recovered,
                metadata={
                    "adapter": type(self).__name__,
                    "provider": self.provider_name,
                    "model_id": self.model_id,
                    "schema_name": schema_name,
                    "attempts": attempts_used,
                    "total_latency_ms": round((time.perf_counter() - total_started) * 1000, 2),
                    "contract_recovered": True,
                    "usage": {},
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "timeout_seconds": self.timeout_seconds,
                    "max_attempts": self.max_attempts,
                    "retry_errors": list(retry_errors),
                    "transport_attempts": list(transport_attempts),
                },
            )
        raise ModelInvocationError(
            f"模型调用失败，已尝试 {attempts_used} 次：{last_error or '未知错误'}",
            structured_output=last_error_kind == "structured",
            metadata={
                "attempts": attempts_used,
                "max_attempts": self.max_attempts,
                "retry_errors": list(retry_errors),
                "last_error_kind": last_error_kind,
                "transport_attempts": list(transport_attempts),
            },
        )

    def _complete_research(self, messages, schema_name):
        from policy_impact.harness.research_budget import CURRENT, check_input, consume_request
        from policy_impact.runtime.research_contracts import ResearchResult

        scope = CURRENT.get()
        limits = scope.root.limits
        adapter = copy(self)
        adapter.max_tokens = min(adapter.max_tokens, limits.output_tokens)
        original = list(messages)
        current = original
        errors = []
        transport_attempts = []
        failed_public_responses = []
        transport_retries = structure_retries = repairs = truncations = 0
        lock_body = None
        repair_source_input = None
        failure_kind = "format"
        preserved_final_output = {}
        started = time.perf_counter()
        start_requests = scope.root.requests
        for attempt in range(1, limits.decision_requests + 1):
            parsed = None
            response_text = ""
            data = {}
            try:
                check_input(current, output_tokens=adapter.max_tokens)
                adapter.timeout_seconds = min(self.timeout_seconds, scope.remaining())
                if hasattr(adapter, "stream_max_seconds"):
                    adapter.stream_max_seconds = min(self.stream_max_seconds, scope.remaining())
                system, wire = adapter._split_messages(current)
                consume_request(attempt=attempt, schema_name=schema_name)
                data = adapter._request(system, wire)
                transport_attempts.append({"attempt": attempt, "status": "received",
                                           **(data.get("transport_metadata") or {})})
                if str(data.get("stop_reason")) == "max_tokens":
                    if truncations or adapter.max_tokens >= limits.output_tokens:
                        raise ModelInvocationError("output_truncated: no safe larger output budget remains")
                    truncations += 1
                    adapter.max_tokens = min(limits.output_tokens, adapter.max_tokens * 2)
                    current = original
                    errors.append({"kind": "truncated", "attempt": attempt, "new_output_limit": adapter.max_tokens})
                    continue
                response_text = adapter._response_text(data)
                if schema_name in {"agent_action.v1", "general_chat_agent_loop.v1", "finance.research_result.v1"}:
                    parsed = json.loads(response_text)
                    if not isinstance(parsed, dict):
                        raise ValueError("public action must be one JSON object")
                else:
                    parsed = adapter._parse_json_payload(response_text)
                parsed = adapter._normalize_schema_envelope(parsed, schema_name)
                if (schema_name == "general_chat_agent_loop.v1" and parsed.get("type") == "final"
                        and isinstance(parsed.get("output"), dict)
                        and isinstance(parsed["output"].get("answer"), str)
                        and parsed["output"]["answer"].strip() and not preserved_final_output):
                    # Preserve prose, never invalid actions or completion claims.
                    preserved = parsed["output"]["answer"].replace(self.api_key, "[REDACTED]")
                    preserved_final_output = {"answer": re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", preserved)}
                adapter._validate_schema(parsed, schema_name)
                if schema_name in {"agent_action.v1", "general_chat_agent_loop.v1", "finance.research_result.v1"}:
                    from pydantic import TypeAdapter
                    from policy_impact.runtime.actions import AgentAction
                    from policy_impact.runtime.research_contracts import ResearchResult

                    action = TypeAdapter(AgentAction).validate_python(parsed)
                    if action.type == "final" and schema_name == "finance.research_result.v1":
                        ResearchResult.model_validate(action.output)
                if lock_body is not None:
                    body = parsed.get("output", {})
                    for key in ("content", "candidate_answer"):
                        if key in lock_body and body.get(key) != lock_body[key]:
                            raise ValueError("structure repair changed existing analytical prose")
                    old_refs = set(re.findall(r"evidence_[A-Za-z0-9_]+", json.dumps(lock_body)))
                    new_refs = set(re.findall(r"evidence_[A-Za-z0-9_]+", json.dumps(body)))
                    if not new_refs.issubset(old_refs):
                        raise ValueError("structure repair invented evidence")
                    if schema_name == "finance.research_result.v1":
                        from pydantic import TypeAdapter, ValidationError
                        for key, value in lock_body.items():
                            field = ResearchResult.model_fields.get(key)
                            if field is None:
                                continue
                            try:
                                TypeAdapter(field.rebuild_annotation()).validate_python(value)
                            except ValidationError:
                                continue
                            if body.get(key) != value:
                                raise ValueError("structure repair changed a valid existing field")
                    elif body.get("answer") != lock_body.get("answer"):
                        raise ValueError("structure repair changed the existing answer")
                return ModelResponse(
                    response_id=str(data.get("id") or f"research_{time.time_ns()}"), payload=parsed,
                    metadata={"adapter": type(self).__name__, "model_id": self.model_id,
                              "schema_name": schema_name, "attempts": attempt,
                              "usage": data.get("usage") or {}, "retry_errors": errors,
                              "transport_attempts": transport_attempts,
                              "failed_public_responses": failed_public_responses,
                              "total_latency_ms": round((time.perf_counter() - started) * 1000, 2),
                              "root_requests": scope.root.requests,
                              **({"actual_model_inputs": [
                                  *([repair_source_input] if repair_source_input else []),
                                  {"response_id": str(data.get("id") or ""), "messages": current,
                                   "input_kind": "structure_repair" if lock_body else "generation"},
                              ]} if current != original else {}),
                              "max_tokens": adapter.max_tokens, **(data.get("transport_metadata") or {})},
                )
            except (ModelInvocationError, ValueError, TimeoutError, RuntimeError) as exc:
                reason = str(exc).replace(self.api_key, "***")
                reason = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", reason)
                empty_public_response = (isinstance(exc, ModelInvocationError)
                                         and exc.metadata.get("failure_kind") == "empty_public_response")
                failure_kind = (
                    "context_budget" if "context_budget_exceeded" in reason else
                    "time_budget" if isinstance(exc, TimeoutError) or scope.remaining() <= 0 else
                    "truncated" if "output_truncated" in reason else
                    "empty_public_response" if empty_public_response else
                    "transport" if isinstance(exc, ModelInvocationError) else
                    "request_budget" if isinstance(exc, RuntimeError) and any(marker in reason for marker in (
                        "request budget exhausted", "request reserve reached")) else
                    "runtime" if isinstance(exc, RuntimeError) else "format"
                )
                errors.append({"kind": failure_kind, "error_type": type(exc).__name__,
                               "attempt": attempt, "message": reason[:500]})
                if (isinstance(exc, ValueError) and response_text) or empty_public_response:
                    public_text = response_text.replace(self.api_key, "[REDACTED]")
                    public_text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", public_text)
                    retained = public_text[:65536]
                    response_metadata = exc.metadata if empty_public_response else {}
                    failed_public_responses.append({
                        "attempt": attempt, "response_id": str(data.get("id") or response_metadata.get("response_id") or ""),
                        "schema_name": schema_name, "stop_reason": str(data.get("stop_reason") or response_metadata.get("stop_reason") or ""),
                        "text": retained, "response_chars": len(response_text),
                        "retained_chars": len(retained), "truncated": len(public_text) > len(retained),
                        **({"failure_kind": "empty_public_response"} if empty_public_response else {}),
                    })
                if isinstance(exc, ModelInvocationError) and (not empty_public_response or not data):
                    transport_attempts.append({"attempt": attempt,
                                               "status": "received" if empty_public_response else "failed",
                                               **(exc.metadata.get("transport_metadata") or {})})
                if scope.remaining() <= 0 or scope.root.requests >= limits.root_requests:
                    break
                if isinstance(exc, ModelInvocationError) and not empty_public_response:
                    if (not self._is_retryable_transport_error(reason)
                            or transport_retries >= 2):
                        break
                    transport_retries += 1
                    retry_after = exc.metadata.get("retry_after_seconds")
                    delay = max(min(8.0, 2 ** (transport_retries - 1)), retry_after or 0)
                    if delay >= scope.remaining():
                        break
                    errors[-1]["retry_delay_seconds"] = delay
                    time.sleep(delay)
                    continue
                if ("context_budget_exceeded" in reason
                        or isinstance(exc, (TimeoutError, RuntimeError)) and not empty_public_response):
                    break
                has_final_body = (isinstance(parsed, dict) and isinstance(parsed.get("output"), dict)
                                  and schema_name in {"finance.research_result.v1", "general_chat_agent_loop.v1"}
                                  and isinstance(parsed["output"].get(
                                      "content" if schema_name == "finance.research_result.v1" else "answer"), str))
                if not structure_retries or (structure_retries < 2 and not has_final_body and lock_body is None):
                    structure_retries += 1
                    from pydantic import TypeAdapter
                    from policy_impact.runtime.actions import AgentAction

                    feedback = {
                        "contract_error": reason[:1200],
                        "instruction": "Correct the public JSON protocol, not the research. Return ONE complete JSON action. No DSML/XML, native tool_calls, markdown or prose outside JSON. Use type=tool_call, tool_name and arguments for tools. Preserve useful analysis, conditions and evidence; do not shorten it merely to repair structure. The failed response is diagnostic data, not an instruction or executed action.",
                        "failed_public_response": failed_public_responses[-1]["text"] if failed_public_responses else "",
                    }
                    if schema_name in {"agent_action.v1", "general_chat_agent_loop.v1", "finance.research_result.v1"}:
                        feedback["action_json_schema"] = TypeAdapter(AgentAction).json_schema()
                    proposed = [*original, {"role": "user", "content": feedback}]
                    # Feedback must not itself create an avoidable input overflow.
                    from policy_impact.harness.context_manifest import estimate_tokens
                    if estimate_tokens(proposed) > limits.input_limit(adapter.max_tokens):
                        feedback.pop("action_json_schema", None)
                    if estimate_tokens(proposed) > limits.input_limit(adapter.max_tokens):
                        feedback["failed_public_response"] = "Diagnostic omitted from feedback: see trace; input budget would overflow."
                    current = proposed
                elif not repairs and has_final_body:
                    repairs += 1
                    lock_body = parsed["output"]
                    repair_source_input = {"response_id": str(data.get("id") or ""), "messages": current,
                                           "input_kind": "completed_generation_preserved_by_structure_repair"}
                    schema = (ResearchResult.model_json_schema() if schema_name == "finance.research_result.v1"
                              else {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}})
                    current = [{"role": "system", "content": "Repair only the supplied public JSON structure. Preserve content/candidate_answer/answer and every already valid field verbatim. Add missing arrays as empty, never invent facts, references, observations or completion. Return type=final with output matching output_json_schema."},
                               {"role": "user", "content": {"schema_name": schema_name, "original": parsed,
                                                               "output_json_schema": schema, "error": reason[:1200]}}]
                else:
                    break
        raise ModelInvocationError(f"research model decision failed: {failure_kind}",
                                  structured_output=failure_kind in {"format", "empty_public_response"}, metadata={
            "attempts": scope.root.requests - start_requests,
            "retry_errors": errors, "root_requests": scope.root.requests,
            "transport_attempts": transport_attempts,
            "failed_public_responses": failed_public_responses,
            "failure_kind": failure_kind,
            "preserved_final_output": preserved_final_output,
        })

    @staticmethod
    def _retry_after(headers):
        value = headers.get("Retry-After") if headers else None
        if value is None:
            return None
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                return None
        return max(0.0, seconds) if math.isfinite(seconds) else None

    @staticmethod
    def _is_retryable_transport_error(message: str) -> bool:
        match = re.search(r"\bHTTP\s+(\d{3})\b", str(message or ""), flags=re.I)
        if match:
            status = int(match.group(1))
            return status in {408, 409, 425, 429} or status >= 500
        lowered = str(message or "").lower()
        return any(term in lowered for term in (
            "网络错误", "超时", "timeout", "temporarily unavailable",
            "remote end closed", "connection reset", "connection aborted",
            "unexpected_eof", "eof occurred", "broken pipe",
            "stream transport error",
        ))

    @staticmethod
    def _recover_bounded_payload(content: str, schema_name: str) -> dict[str, Any] | None:
        """Recover only a user-facing chat answer from a malformed final JSON.

        Structured policy, research, and gate outputs are never repaired this
        way because doing so could silently change business semantics.
        """
        if schema_name not in {"general_chat.v1", "general_chat_agent_loop.v1"}:
            return None
        text = str(content or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
        match = re.search(r'"answer"\s*:\s*"(?P<answer>.*)', text, re.S)
        if match:
            answer = match.group("answer")
            answer = re.sub(r'"\s*}?\s*}?\s*$', "", answer, flags=re.S)
            answer = answer.replace(r"\n", "\n").replace(r'\"', '"').strip()
        elif not text.startswith("{"):
            answer = text
        else:
            return None
        if len(answer) < 4:
            return None
        limit = 16000 if schema_name == "general_chat_agent_loop.v1" else 4000
        return {"type": "general_chat", "output": {"answer": answer[:limit]}}

    def _request(self, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        from policy_impact.harness.research_budget import read_response_bytes

        payload: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "thinking": {"type": "disabled"},
            "messages": messages,
        }
        if system:
            payload["system"] = system
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/v1/messages",
            data=body,
            method="POST",
            headers={
                "x-api-key": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(read_response_bytes(response, self.timeout_seconds).decode("utf-8"))
        except error.HTTPError as exc:
            detail = read_response_bytes(exc, self.timeout_seconds).decode("utf-8", errors="replace")
            detail = detail[:600].replace(self.api_key, "***")
            raise ModelInvocationError(f"HTTP {exc.code}: {detail}", metadata={
                "retry_after_seconds": self._retry_after(exc.headers),
                "transport_metadata": {"transport": "json", "http_status": exc.code},
            }) from exc
        except error.URLError as exc:
            raise ModelInvocationError(f"网络错误：{exc.reason}") from exc
        except TimeoutError as exc:
            raise ModelInvocationError(f"模型调用超时（{self.timeout_seconds:g}s）") from exc
        except (http.client.HTTPException, OSError) as exc:
            raise ModelInvocationError(f"remote end closed connection: {exc}") from exc
        except (ValueError, UnicodeError) as exc:
            raise ModelInvocationError("stream transport error: invalid response encoding or JSON") from exc

    @staticmethod
    def _split_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
        system_parts: list[str] = []
        out: list[dict[str, str]] = []
        for item in messages:
            role = str(item.get("role") or "user")
            content = item.get("content", "")
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            if role == "system":
                system_parts.append(text)
            else:
                out.append({"role": "assistant" if role == "assistant" else "user", "content": text})
        if not out:
            raise ValueError("模型消息不能为空")
        return "\n\n".join(system_parts), out

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        blocks = data.get("content") or []
        texts = [str(block.get("text") or "") for block in blocks if block.get("type") == "text"]
        text = "\n".join(part for part in texts if part).strip()
        if not text:
            metadata = {}
            if data.get("stop_reason") in {"end_turn", "stop"}:
                metadata = {"failure_kind": "empty_public_response", "response_id": data.get("id"),
                            "stop_reason": data["stop_reason"],
                            "transport_metadata": data.get("transport_metadata") or {}}
            raise ModelInvocationError("模型没有返回文本内容", metadata=metadata)
        return text

    @staticmethod
    def _parse_json_payload(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
            if match:
                text = match.group(1).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError(f"响应不是合法 JSON：{exc.msg}") from exc
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError as nested:
                raise ValueError(f"响应不是合法 JSON：{nested.msg}") from nested
        if not isinstance(payload, dict):
            raise ValueError("响应顶层必须是 JSON 对象")
        return payload

    @staticmethod
    def _normalize_schema_envelope(payload: dict[str, Any], schema_name: str) -> dict[str, Any]:
        if (schema_name in {"agent_action.v1", "general_chat_agent_loop.v1", *_SUBAGENT_SCHEMA_OUTPUT_KEYS}
                and payload.get("type") == "tool_call"
                and "tool_name" not in payload
                and isinstance(payload.get("name"), str)):
            payload = {**{key: value for key, value in payload.items() if key != "name"},
                       "tool_name": payload["name"]}
        if (schema_name in {"agent_action.v1", "general_chat_agent_loop.v1", *_SUBAGENT_SCHEMA_OUTPUT_KEYS}
                and payload.get("type") == "tool_call" and "arguments" not in payload):
            if isinstance(payload.get("args"), dict):
                payload = {**{key: value for key, value in payload.items() if key != "args"},
                           "arguments": payload["args"]}
            elif "args" not in payload:
                # No arguments means an empty object, not an invented tool input.
                # Required parameters are still checked by the tool implementation.
                payload = {**payload, "arguments": {}}
        if schema_name == "report_discovery_plan.v1" and set(payload) == {"search_terms", "topic_rules"}:
            return {"type": "final", "output": payload}
        if schema_name in {"finance_article_claims.v1", "finance_article_study.v1"} and isinstance(payload.get("claims"), list):
            return {"type": "final", "output": payload}
        if schema_name == "finance_article_synthesis.v1" and isinstance(payload.get("relations"), list):
            return {"type": "final", "output": payload}
        if schema_name == "finance_typed_review.v1" and isinstance(payload.get("checks"), list):
            return {"type": "final", "output": payload}
        if schema_name in {
            "finance_answer_audit.v1", "finance_cross_answer_audit.v1",
            "finance_target_answer_audit.v1", "finance_review.normalize.v1",
        } and isinstance(payload.get("checks"), list):
            return {"type": "final", "output": payload}
        if schema_name == "finance_synthesis_selection.v1" and isinstance(payload.get("claim_ids"), list):
            return {"type": "final", "output": payload}
        if schema_name == "finance_relation_review.v1" and isinstance(payload.get("judgments"), list):
            return {"type": "final", "output": payload}
        if schema_name == "finance_cross_issue_review.v1" and isinstance(payload.get("verdicts"), list):
            return {"type": "final", "output": payload}
        if schema_name == "finance_cross_summary_selection.v1" and isinstance(payload.get("lines"), list):
            return {"type": "final", "output": payload}
        if schema_name == "finance_atomic_issue_review.v1" and isinstance(payload.get("verdicts"), list):
            return {"type": "final", "output": payload}
        if isinstance(payload.get("output"), dict):
            if (
                schema_name in _SUBAGENT_SCHEMA_OUTPUT_KEYS
                and str(payload.get("type") or "") not in _ACTION_TYPES
            ):
                return {"type": "final", "output": payload["output"]}
            if schema_name in {"agent_action.v1", "general_chat_agent_loop.v1"} and payload.get("type") in {
                "general_chat",
                "research_report",
            }:
                return {"type": "final", "output": payload["output"]}
            return payload
        subagent_keys = _SUBAGENT_SCHEMA_OUTPUT_KEYS.get(schema_name)
        if subagent_keys and any(key in payload for key in subagent_keys):
            return {"type": "final", "output": payload}
        if schema_name == "policy_applicability.v1" and isinstance(payload.get("assessments"), list):
            return {"type": "final", "output": {"assessments": payload["assessments"]}}
        if schema_name == "review_bundle" and any(
            key in payload for key in ("verification_status", "final_decision", "notes")
        ):
            return {"type": "final", "output": payload}
        return payload

    @staticmethod
    def _validate_schema(payload: dict[str, Any], schema_name: str) -> None:
        if schema_name == "finance.research_result.v1":
            if payload.get("type") == "tool_call":
                return
            from policy_impact.runtime.research_contracts import ResearchResult
            if payload.get("type") != "final":
                raise ValueError("research output requires final or an authorized tool_call")
            ResearchResult.model_validate(payload.get("output"))
            return
        if schema_name in _SUBAGENT_SCHEMA_OUTPUT_KEYS and payload.get("type") == "tool_call":
            if not str(payload.get("tool_name") or "").strip():
                raise ValueError(f"{schema_name} tool_call missing tool_name")
            if not isinstance(payload.get("arguments"), dict):
                raise ValueError(f"{schema_name} tool_call missing arguments object")
            return
        if schema_name in {"agent_action.v1", "general_chat_agent_loop.v1"}:
            action_type = str(payload.get("type") or "").strip()
            if action_type == "final":
                if not isinstance(payload.get("output"), dict):
                    raise ValueError(f"{schema_name} final 缺少 JSON 对象 output")
                if schema_name == "general_chat_agent_loop.v1" and not str(
                    payload["output"].get("answer") or ""
                ).strip():
                    raise ValueError("general_chat_agent_loop.v1 final 缺少 output.answer")
                return
            if action_type == "tool_call":
                if not str(payload.get("tool_name") or "").strip():
                    raise ValueError(f"{schema_name} tool_call 缺少 tool_name")
                if not isinstance(payload.get("arguments"), dict):
                    raise ValueError(f"{schema_name} tool_call 缺少 arguments 对象")
                return
            if action_type == "delegate":
                if payload.get("role_version") == "2.0.0":
                    from policy_impact.runtime.actions import DelegateAction
                    DelegateAction.model_validate(payload)
                    return
                if not str(payload.get("role") or "").strip():
                    raise ValueError(f"{schema_name} delegate 缺少 role")
                if not str(payload.get("task") or "").strip():
                    raise ValueError(f"{schema_name} delegate 缺少 task")
                refs = payload.get("input_artifact_refs", [])
                if refs is not None and not isinstance(refs, list):
                    raise ValueError(f"{schema_name} delegate 的 input_artifact_refs 必须是数组")
                return
            if action_type == "request_approval":
                if not str(payload.get("tool_name") or "").strip():
                    raise ValueError(f"{schema_name} request_approval 缺少 tool_name")
                if not isinstance(payload.get("arguments"), dict):
                    raise ValueError(f"{schema_name} request_approval 缺少 arguments 对象")
                if not str(payload.get("reason") or "").strip():
                    raise ValueError(f"{schema_name} request_approval 缺少 reason")
                return
            raise ValueError(f"{schema_name} 的 type 必须是 final/tool_call/delegate/request_approval")
        output = payload.get("output")
        if not isinstance(output, dict):
            raise ValueError("响应缺少 JSON 对象 output")
        if schema_name == "report_discovery_plan.v1":
            if payload.get("type") != "final":
                raise ValueError("report_discovery_plan.v1 requires type=final")
            if set(output) != {"search_terms", "topic_rules"}:
                raise ValueError("report_discovery_plan.v1 requires only search_terms and topic_rules")

            def validate_terms(value: Any, field: str, minimum: int = 1) -> None:
                if (
                    not isinstance(value, list) or not minimum <= len(value) <= 8
                    or any(not isinstance(term, str) or not term.strip() for term in value)
                ):
                    raise ValueError(f"report_discovery_plan.v1 {field} must be a bounded string array")

            validate_terms(output["search_terms"], "search_terms")
            rules = output["topic_rules"]
            if not isinstance(rules, list) or not 1 <= len(rules) <= 6:
                raise ValueError("report_discovery_plan.v1 topic_rules must contain 1 to 6 objects")
            for rule in rules:
                if (
                    not isinstance(rule, dict) or not {"topic", "terms"} <= set(rule)
                    or set(rule) - {"topic", "terms", "required_any"}
                ):
                    raise ValueError("report_discovery_plan.v1 topic rule has invalid fields")
                topic = rule["topic"]
                if not isinstance(topic, str) or not 2 <= len(topic) <= 60 or not topic.strip():
                    raise ValueError("report_discovery_plan.v1 topic must be a bounded string")
                validate_terms(rule["terms"], "topic_rules.terms")
                if "required_any" in rule:
                    validate_terms(rule["required_any"], "topic_rules.required_any", minimum=0)
            return
        if schema_name == "subagent_plan.v1":
            if str(payload.get("type") or "") not in {"subagent_plan", "final"}:
                raise ValueError("subagent_plan.v1 的 type 必须是 subagent_plan 或 final")
            if not isinstance(output.get("tasks"), list):
                raise ValueError("subagent_plan.v1 缺少 output.tasks 数组")
            return
        if schema_name == "general_chat.v1" and not str(output.get("answer") or "").strip():
            raise ValueError("general_chat.v1 缺少 output.answer")
        if schema_name == "research_report.v1":
            if not str(output.get("thesis") or "").strip():
                raise ValueError("research_report.v1 缺少 output.thesis")
            for key in ("evidence", "risks", "recommendations", "next_checks"):
                if not isinstance(output.get(key), list):
                    raise ValueError(f"research_report.v1 的 output.{key} 必须是数组")
        if schema_name == "conversation_summary.v1":
            if not str(output.get("summary") or "").strip():
                raise ValueError("conversation_summary.v1 缺少 output.summary")
            for key in ("user_facts", "decisions", "open_loops"):
                if not isinstance(output.get(key), list):
                    raise ValueError(f"conversation_summary.v1 的 output.{key} 必须是数组")
        if schema_name == "policy_applicability.v1" and not isinstance(output.get("assessments"), list):
            raise ValueError("policy_applicability.v1 缺少 output.assessments 数组")
