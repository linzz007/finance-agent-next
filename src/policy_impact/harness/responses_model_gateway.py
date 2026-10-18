"""Responses text transport; public JSON actions still use the shared validator."""

from __future__ import annotations

import json
import time
from typing import Any

from policy_impact.harness.model_gateway import ModelInvocationError
from policy_impact.harness.openai_model_gateway import OpenAICompatibleModelAdapter


class ResponsesModelAdapter(OpenAICompatibleModelAdapter):
    provider_name = "openai-responses"
    api_style = "responses"
    endpoint_path = "/responses"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.thinking is not None or self.reasoning_effort is not None:
            raise ValueError("Responses does not support DeepSeek thinking settings")

    def _build_payload(self, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self.model_id, "input": messages,
            "max_output_tokens": self.max_tokens, "temperature": self.temperature,
            "stream": self.stream, "store": False,
        }
        if system:
            payload["instructions"] = system
        return payload

    @staticmethod
    def _public_parts(data: dict[str, Any]) -> dict[tuple[int, int], tuple[str, str]]:
        output = data.get("output")
        if not isinstance(output, list):
            raise ModelInvocationError("stream transport error: Responses output must be an array")
        parts = {}
        for output_index, item in enumerate(output):
            if not isinstance(item, dict):
                raise ModelInvocationError("stream transport error: invalid Responses output item")
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message" or item.get("role") != "assistant":
                raise ModelInvocationError("provider response rejected: unsupported non-text Responses output")
            # Truncated messages are allowed only in a response already marked incomplete.
            expected = {"completed"} if data.get("status") == "completed" else {"completed", "incomplete"}
            if (not isinstance(item.get("status"), str) or item["status"] not in expected
                    or not isinstance(item.get("content"), list)):
                raise ModelInvocationError("stream transport error: invalid Responses message status/content")
            for content_index, part in enumerate(item["content"]):
                if not isinstance(part, dict):
                    raise ModelInvocationError("stream transport error: invalid Responses content part")
                if part.get("type") == "refusal":
                    raise ModelInvocationError("provider response rejected: refusal")
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise ModelInvocationError("provider response rejected: unsupported Responses content type")
                parts[output_index, content_index] = (str(item.get("id") or ""), part["text"])
        return parts

    def _normalize_response(self, data: Any, transport: dict[str, Any], started: float) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ModelInvocationError("stream transport error: Responses body must be an object")
        state = data.get("status")
        transport.update(response_id=data.get("id"), returned_model_id=data.get("model") or self.model_id,
                         completion_state=str(state or "missing_status")[:80])
        if not isinstance(state, str) or state not in {"completed", "incomplete", "failed"}:
            raise ModelInvocationError("stream transport error: Responses missing terminal status")
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise ModelInvocationError("stream transport error: Responses missing response id")
        transport["response_complete"] = True
        if state == "failed" or data.get("error"):
            self._raise_provider_error(data.get("error"))
        details = data.get("incomplete_details") or {}
        if not isinstance(details, dict):
            raise ModelInvocationError("stream transport error: invalid incomplete_details")
        reason = details.get("reason")
        if state == "incomplete":
            transport["incomplete_reason"] = str(reason or "unknown")[:80]
            if reason != "max_output_tokens":
                raise ModelInvocationError("provider response incomplete: " + str(reason or "unknown")[:80])
            # Never parse or preserve even a syntactically closed truncated action.
            content = ""
        else:
            if details:
                raise ModelInvocationError("stream transport error: completed Responses has incomplete_details")
            content = "".join(text for _, text in self._public_parts(data).values()).strip()
            if not content:
                raise ModelInvocationError("Responses completed with empty public text", metadata={
                    "failure_kind": "empty_public_response", "response_id": data["id"], "stop_reason": "stop",
                })
        return {
            "id": data["id"], "model": data.get("model") or self.model_id,
            "stop_reason": "max_tokens" if state == "incomplete" else "stop",
            "content": [{"type": "text", "text": content}], "usage": data.get("usage") or {},
            "transport_metadata": transport,
        }

    def _raise_provider_error(self, problem: Any) -> None:
        if not isinstance(problem, dict):
            raise ModelInvocationError("provider response failed: unspecified Responses error")
        code = str(problem.get("status") or problem.get("code") or "")
        message = str(problem.get("message") or "").replace(self.api_key, "***")[:400]
        mapped = {"server_error": 500, "rate_limit_exceeded": 429,
                  "invalid_api_key": 401, "insufficient_quota": 402}
        status = int(code) if code.isdecimal() and 400 <= int(code) <= 599 else mapped.get(code)
        prefix = f"HTTP {status}" if status else "provider response failed"
        raise ModelInvocationError(f"{prefix}: {message}")

    def _read_stream(self, response: Any, metadata: dict[str, Any], started: float) -> dict[str, Any]:
        data_lines: list[str] = []
        event_name = ""
        event_bytes = 0
        response_id = ""
        sequence = -1
        first_line = True
        fragments: dict[tuple[int, int], list[str]] = {}
        item_ids: dict[tuple[int, int], str] = {}
        for line in self._stream_lines(response, started, 1024 * 1024, 8 * 1024 * 1024):
            event_bytes += len(line)
            if event_bytes > 1024 * 1024:
                raise ModelInvocationError("stream transport error: response size limit exceeded")
            text = line.decode("utf-8").rstrip("\r\n")
            if first_line:
                text = text.removeprefix("\ufeff")
                first_line = False
            if text:
                key, separator, value = text.partition(":")
                if separator and key == "data":
                    data_lines.append(value.removeprefix(" "))
                elif separator and key == "event":
                    event_name = value.removeprefix(" ")
                continue
            event_bytes = 0
            if not data_lines:
                event_name = ""
                continue
            metadata["stream_event_count"] += 1
            event = json.loads("\n".join(data_lines))
            data_lines = []
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ModelInvocationError("stream transport error: invalid Responses event")
            kind = event["type"]
            if event_name and event_name != kind:
                raise ModelInvocationError("stream transport error: Responses event/type mismatch")
            event_name = ""
            if "sequence_number" in event:
                number = event["sequence_number"]
                if type(number) is not int or number <= sequence:
                    raise ModelInvocationError("stream transport error: Responses sequence out of order")
                sequence = number
            if kind == "error":
                self._raise_provider_error(event.get("error") or event)
            snapshot = event.get("response")
            if isinstance(snapshot, dict):
                new_id = snapshot.get("id")
                if not isinstance(new_id, str) or not new_id or (response_id and new_id != response_id):
                    raise ModelInvocationError("stream transport error: Responses id mismatch")
                response_id = new_id
                metadata["response_id"] = response_id
                if snapshot.get("status") in ("queued", "in_progress"):
                    metadata["completion_state"] = snapshot["status"]
            if kind == "response.output_text.delta":
                key = (event.get("output_index"), event.get("content_index"))
                delta = event.get("delta")
                item_id = event.get("item_id")
                if (any(type(index) is not int or index < 0 for index in key)
                        or not isinstance(delta, str) or not isinstance(item_id, str) or not item_id
                        or (key in item_ids and item_ids[key] != item_id)):
                    raise ModelInvocationError("stream transport error: invalid Responses text delta")
                item_ids[key] = item_id
                fragments.setdefault(key, []).append(delta)
                if delta:
                    metadata["stream_content_chars"] += len(delta)
                    if metadata["time_to_first_token_ms"] is None:
                        metadata["time_to_first_token_ms"] = round((time.perf_counter() - started) * 1000, 2)
            if kind in {"response.completed", "response.incomplete", "response.failed"}:
                state = kind.removeprefix("response.")
                if not isinstance(snapshot, dict) or snapshot.get("status") != state:
                    raise ModelInvocationError("stream transport error: Responses terminal/status mismatch")
                metadata.update(stream_complete=True, response_complete=True, completion_state=state,
                                stream_duration_ms=round((time.perf_counter() - started) * 1000, 2))
                if state == "completed":
                    try:
                        parts = self._public_parts(snapshot)
                    except ModelInvocationError as exc:
                        if str(exc).startswith("stream transport error"):
                            metadata.update(stream_complete=False, response_complete=False)
                        raise
                    for key, value in fragments.items():
                        if parts.get(key) != (item_ids[key], "".join(value)):
                            metadata.update(stream_complete=False, response_complete=False)
                            raise ModelInvocationError("stream transport error: Responses delta/final text mismatch")
                    if parts and metadata["time_to_first_token_ms"] is None:
                        metadata["time_to_first_token_ms"] = round((time.perf_counter() - started) * 1000, 2)
                return snapshot
        raise ModelInvocationError("stream transport error: EOF before Responses terminal event")
