"""OpenAI-compatible model adapter used by the Workbench model policy."""

from __future__ import annotations

import http.client
import json
import re
import time
from typing import Any
from urllib import error, request

from policy_impact.harness.model_config import effective_model_config
from policy_impact.harness.model_gateway import (
    AnthropicCompatibleModelAdapter,
    ModelInvocationError,
)


class OpenAICompatibleModelAdapter(AnthropicCompatibleModelAdapter):
    """Call an OpenAI-compatible ``/chat/completions`` endpoint.

    The response is normalized to the internal shape expected by the shared
    retry, schema, trace, and finance workflow code.
    """

    provider_name = "openai-compatible"
    api_style = "chat_completions"
    endpoint_path = "/chat/completions"

    def __init__(
        self, *, stream: bool = False, stream_max_seconds: float = 600,
        thinking: bool | None = None, reasoning_effort: str | None = None,
        profile_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not isinstance(stream, bool):
            raise ValueError("stream must be a boolean")
        if not 1 <= stream_max_seconds <= 900:
            raise ValueError("stream_max_seconds must be between 1 and 900")
        self.stream = stream
        self.stream_max_seconds = stream_max_seconds
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError("thinking must be a boolean or None")
        if reasoning_effort is not None and (reasoning_effort not in {"low", "high", "max"} or thinking is not True):
            raise ValueError("reasoning_effort requires thinking enabled and low, high or max")
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.profile_id = profile_id

    @classmethod
    def from_env(cls) -> OpenAICompatibleModelAdapter:
        config = effective_model_config()
        return cls(
            model_id=config["model_id"],
            base_url=config["base_url"],
            api_key=config["api_key"],
            max_tokens=config["max_tokens"],
            temperature=config["temperature"],
            timeout_seconds=config["timeout_seconds"],
            max_attempts=config["max_attempts"],
            stream=config["stream"],
            stream_max_seconds=config["stream_max_seconds"],
            thinking=config["thinking"],
            reasoning_effort=config["reasoning_effort"],
            profile_id=config["profile_id"],
        )

    def _request(self, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = self._build_payload(system, messages)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{self.endpoint_path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "text/event-stream" if self.stream else "application/json",
                "User-Agent": "policy-impact-workbench/1.0",
            },
        )
        started = time.perf_counter()
        transport = {
            "api_style": self.api_style,
            "transport": "sse" if self.stream else "json",
            "stream": self.stream,
            "response_complete": False,
            "completion_state": "not_received",
            "model_profile": self.profile_id,
            "requested_model_id": self.model_id,
            "thinking_enabled": self.thinking,
            "reasoning_effort": self.reasoning_effort,
        }
        if self.stream:
            transport.update({
                "stream_complete": False, "stream_event_count": 0,
                "stream_content_chars": 0, "time_to_first_token_ms": None,
                "stream_max_seconds": self.stream_max_seconds,
            })
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                if self.stream:
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                    if content_type.strip().lower() != "text/event-stream":
                        raise ModelInvocationError("stream transport error: expected text/event-stream")
                    data = self._read_stream(response, transport, started)
                else:
                    data = self._read_json(response, started)
            return self._normalize_response(data, transport, started)
        except error.HTTPError as exc:
            # Error bodies are diagnostic only and must not extend the response deadline.
            try:
                detail = self._read_error_detail(exc, started)
            except (OSError, ValueError, http.client.HTTPException):
                detail = "unreadable provider error body"
            finally:
                exc.close()
            transport["http_status"] = exc.code
            failure = self._transport_error(f"HTTP {exc.code}: {detail}", transport, started)
            failure.metadata["retry_after_seconds"] = self._retry_after(exc.headers)
            raise failure from exc
        except error.URLError as exc:
            raise self._transport_error(f"网络错误：{exc.reason}", transport, started) from exc
        except TimeoutError as exc:
            raise self._transport_error(
                f"模型调用超时（idle={self.timeout_seconds:g}s, stream={self.stream_max_seconds:g}s）",
                transport, started,
            ) from exc
        except (http.client.HTTPException, OSError) as exc:
            raise self._transport_error(
                f"remote end closed connection; retryable transport error: {exc}",
                transport, started,
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise self._transport_error(
                f"stream transport error: invalid response encoding or JSON ({type(exc).__name__})",
                transport, started,
            ) from exc
        except ModelInvocationError as exc:
            failure = self._transport_error(str(exc), transport, started)
            failure.metadata.update({key: value for key, value in exc.metadata.items() if key != "transport_metadata"})
            raise failure from exc

    def _read_json(self, response: Any, started: float) -> dict[str, Any]:
        # JSON need not end in a newline; _stream_lines also yields an EOF tail.
        return json.loads(b"".join(self._stream_lines(
            response, started, 8 * 1024 * 1024, 8 * 1024 * 1024)).decode("utf-8"))

    def _read_error_detail(self, response: Any, started: float) -> str:
        chunks = []
        for line in self._stream_lines(response, started, 65536, 65536):
            chunks.append(line)
            if sum(map(len, chunks)) >= 4096:
                break
        return b"".join(chunks).decode("utf-8", errors="replace").replace(self.api_key, "***")[:600]

    def _build_payload(self, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        openai_messages = list(messages)
        if system:
            openai_messages.insert(0, {"role": "system", "content": system})
        payload = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": openai_messages,
        }
        if self.thinking is not None:
            payload["thinking"] = {"type": "enabled" if self.thinking else "disabled"}
        if self.thinking:
            payload.pop("temperature")
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _normalize_response(self, data: Any, transport: dict[str, Any], started: float) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise self._transport_error("OpenAI-compatible 响应不是 JSON 对象", transport, started)
        transport["returned_model_id"] = data.get("model") or self.model_id
        choices = data.get("choices") or []
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise self._transport_error("OpenAI-compatible 响应缺少 choices", transport, started)
        choice = choices[0]
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason in {"stop", "length", "content_filter", "tool_calls", "function_call"}:
            transport.update(response_complete=True, response_id=data.get("id"))
        if not isinstance(reason, str) or reason not in {"stop", "length"}:
            transport["completion_state"] = str(reason or "missing_finish_reason")[:80]
            raise self._transport_error("provider response rejected: missing or unsupported finish_reason", transport, started)
        transport.update(response_complete=True, completion_state="completed" if reason == "stop" else "incomplete",
                         response_id=data.get("id"))
        if reason == "length":
            transport["incomplete_reason"] = "max_output_tokens"
        message = choice.get("message") or {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or item.get("content") or "")
                for item in content
                if isinstance(item, dict)
            )
        content = str(content or "").strip()
        if not content and choice.get("finish_reason") != "length":
            failure = self._transport_error("OpenAI-compatible 响应缺少 message.content", transport, started)
            if choice.get("finish_reason") == "stop":
                failure.metadata.update(failure_kind="empty_public_response",
                                        response_id=data.get("id"), stop_reason="stop")
            raise failure
        return {
            "id": data.get("id"),
            "model": data.get("model") or self.model_id,
            "stop_reason": (
                "max_tokens" if choice.get("finish_reason") == "length"
                else str(choice.get("finish_reason") or "")
            ),
            "content": [{"type": "text", "text": content}],
            "usage": data.get("usage") or {},
            "transport_metadata": transport,
        }

    def _transport_error(
        self, message: str, metadata: dict[str, Any], started: float,
    ) -> ModelInvocationError:
        if self.stream:
            metadata["stream_duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return ModelInvocationError(
            message.replace(self.api_key, "***"), metadata={"transport_metadata": dict(metadata)},
        )

    def _read_stream(
        self, response: Any, metadata: dict[str, Any], started: float,
    ) -> dict[str, Any]:
        # SSE data is delimited by blank lines, not by network reads or JSON chunks.
        event_limit, stream_limit = 1024 * 1024, 8 * 1024 * 1024
        data_lines: list[str] = []
        content: list[str] = []
        event_bytes = total_bytes = 0
        response_id = model = finish_reason = ""
        usage: dict[str, Any] = {}
        first_line = True
        for line in self._stream_lines(response, started, event_limit, stream_limit):
            event_bytes += len(line)
            total_bytes += len(line)
            if event_bytes > event_limit or total_bytes > stream_limit:
                raise ModelInvocationError("stream transport error: response size limit exceeded")
            text = line.decode("utf-8").rstrip("\r\n")
            if first_line:
                text = text.removeprefix("\ufeff")
                first_line = False
            if text:
                field, separator, value = text.partition(":")
                if field == "data" and separator:
                    data_lines.append(value.removeprefix(" "))
                continue
            event_bytes = 0
            if not data_lines:
                continue
            event_data = "\n".join(data_lines)
            data_lines = []
            metadata["stream_event_count"] += 1
            if event_data == "[DONE]":
                if not finish_reason:
                    raise ModelInvocationError("stream transport error: [DONE] without finish_reason")
                if finish_reason in {"stop", "length", "content_filter", "tool_calls", "function_call"}:
                    metadata.update(stream_complete=True, response_complete=True, completion_state=finish_reason)
                if finish_reason not in {"stop", "length"}:
                    raise ModelInvocationError(f"unsupported stream finish_reason: {finish_reason[:60]}")
                metadata.update({
                    "stream_complete": True,
                    "stream_duration_ms": round((time.perf_counter() - started) * 1000, 2),
                })
                return {
                    "id": response_id, "model": model or self.model_id, "usage": usage,
                    "choices": [{"finish_reason": finish_reason, "message": {"content": "".join(content)}}],
                }
            chunk = json.loads(event_data)
            if not isinstance(chunk, dict):
                raise ModelInvocationError("stream transport error: event must be a JSON object")
            if chunk.get("error"):
                provider_error = chunk["error"]
                detail = ""
                if isinstance(provider_error, dict):
                    status = str(provider_error.get("status") or provider_error.get("code") or "")
                    detail = "; ".join(
                        str(provider_error[key]).replace(self.api_key, "***")[:240]
                        for key in ("code", "type", "message") if provider_error.get(key)
                    )
                    if status.isdecimal() and 400 <= int(status) <= 599:
                        raise ModelInvocationError(f"HTTP {status}: streamed provider error: {detail}")
                raise ModelInvocationError(f"stream transport error: provider returned an error event: {detail}")
            response_id = str(chunk.get("id") or response_id)
            model = str(chunk.get("model") or model)
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not isinstance(choices, list):
                raise ModelInvocationError("stream transport error: invalid choices")
            for choice in choices:
                if not isinstance(choice, dict):
                    raise ModelInvocationError("stream transport error: invalid choice")
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    raise ModelInvocationError("stream transport error: invalid delta")
                fragment = delta.get("content")
                if fragment is not None:
                    if not isinstance(fragment, str) or (fragment and finish_reason):
                        raise ModelInvocationError("stream transport error: invalid content delta")
                    if fragment:
                        if metadata["time_to_first_token_ms"] is None:
                            metadata["time_to_first_token_ms"] = round(
                                (time.perf_counter() - started) * 1000, 2,
                            )
                        content.append(fragment)
                        metadata["stream_content_chars"] += len(fragment)
                reason = choice.get("finish_reason")
                if reason is not None:
                    if not isinstance(reason, str) or (finish_reason and finish_reason != reason):
                        raise ModelInvocationError("stream transport error: inconsistent finish_reason")
                    finish_reason = reason
        raise ModelInvocationError("stream transport error: EOF before [DONE]")

    def _stream_lines(self, response: Any, started: float, line_limit: int, total_limit: int):
        pending = b""
        total_bytes = 0
        while True:
            elapsed = time.perf_counter() - started
            if elapsed >= self.stream_max_seconds:
                raise TimeoutError("stream total duration exceeded")
            # read1 returns available bytes, so an unfinished line cannot hide
            # a continuously dribbling response from the elapsed-time checks.
            from policy_impact.harness.research_budget import tighten_response_timeout
            tighten_response_timeout(response, self.timeout_seconds,
                                     remaining_seconds=self.stream_max_seconds - elapsed)
            chunk = response.read1(64 * 1024)
            if time.perf_counter() - started >= self.stream_max_seconds:
                raise TimeoutError("stream total duration exceeded")
            if not chunk:
                if pending:
                    yield pending
                return
            total_bytes += len(chunk)
            if total_bytes > total_limit:
                raise ModelInvocationError("stream transport error: response size limit exceeded")
            pending += chunk
            start = 0
            for newline in re.finditer(rb"\r\n|\r|\n", pending):
                if newline.group() == b"\r" and newline.end() == len(pending):
                    break
                yield pending[start:newline.end()]
                start = newline.end()
            pending = pending[start:]
            if len(pending) > line_limit:
                raise ModelInvocationError("stream transport error: response size limit exceeded")
