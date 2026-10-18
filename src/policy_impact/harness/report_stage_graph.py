"""State, graph transitions and gates for the two report workflows.

This is deliberately separate from PolicyImpactGraph: that graph owns the
legacy policy stages, while these stages describe report execution contracts.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


END = "done"


class ReportStageError(ValueError):
    """A stage was entered out of order or its output failed a gate."""


@dataclass(frozen=True)
class StageSpec:
    name: str
    next_on_allow: str
    next_on_ask: str | None = None
    required_outputs: tuple[str, ...] = ()
    check: Callable[[Mapping[str, Any]], str | None] | None = None


@dataclass
class ReportState:
    workflow: str
    run_id: str
    current_stage: str
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    transitions: list[dict[str, str]] = field(default_factory=list)
    status: str = "running"

    def require(self, stage: str, key: str) -> Any:
        if stage not in self.outputs or key not in self.outputs[stage]:
            raise ReportStageError(f"{self.workflow}: {stage}.{key} is not available")
        return deepcopy(self.outputs[stage][key])

    def require_value(self, key: str) -> Any:
        if key not in self.values:
            raise ReportStageError(f"{self.workflow}: state value {key} is not available")
        return deepcopy(self.values[key])

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": "report.stage_graph.v1",
            "workflow": self.workflow,
            "run_id": self.run_id,
            "status": self.status,
            "current_stage": self.current_stage,
            "transitions": list(self.transitions),
            "stage_output_keys": {name: sorted(value) for name, value in self.outputs.items()},
            "state_value_keys": sorted(self.values),
        }


class ReportStageGraph:
    """Choose the next node only after the current node's output gate passes."""

    def __init__(self, workflow: str, run_id: str, specs: tuple[StageSpec, ...]) -> None:
        if not specs or len({item.name for item in specs}) != len(specs):
            raise ValueError("report graph needs unique, non-empty stages")
        names = {item.name for item in specs}
        for item in specs:
            if item.next_on_allow not in names | {END}:
                raise ValueError(f"{item.name}: unknown allow transition")
            if item.next_on_ask is not None and item.next_on_ask not in names | {END}:
                raise ValueError(f"{item.name}: unknown ask transition")
        self.specs = {item.name: item for item in specs}
        self.state = ReportState(workflow, run_id, specs[0].name)
        self._active: str | None = None

    def start(self, name: str) -> None:
        if self.state.status != "running" or self.state.current_stage != name or self._active:
            raise ReportStageError(f"{self.state.workflow}: cannot start {name}; expected {self.state.current_stage}")
        self._active = name

    def fail(self, name: str, reason: str) -> dict[str, str]:
        if self._active != name:
            raise ReportStageError(f"{self.state.workflow}: cannot fail {name}; active stage is {self._active}")
        transition = {"from": name, "to": "failed", "decision": "deny", "reason": reason}
        self.state.transitions.append(transition)
        self.state.status = "blocked"
        self.state.current_stage = "failed"
        self._active = None
        return transition

    def finish(
        self,
        name: str,
        outputs: Mapping[str, Any],
        gate: Mapping[str, Any] | None = None,
        *,
        skipped: bool = False,
        values: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        if self._active != name:
            raise ReportStageError(f"{self.state.workflow}: completing {name} while {self._active} is active")
        spec = self.specs[name]
        output = deepcopy(dict(outputs))
        decision = str((gate or {}).get("decision") or "allow")
        if decision not in {"allow", "ask", "deny"}:
            raise ReportStageError(f"{name}: invalid gate decision {decision}")
        if not skipped and decision != "deny":
            missing = [key for key in spec.required_outputs if key not in output]
            if missing:
                decision = "deny"
                reason = "missing stage output: " + ", ".join(missing)
            else:
                reason = spec.check(output) if spec.check else None
                if reason:
                    decision = "deny"
        else:
            reason = str((gate or {}).get("reason") or "stage denied")
        if decision == "deny" and not skipped:
            self.fail(name, reason)
            raise ReportStageError(f"{self.state.workflow}.{name}: {reason}")
        if skipped:
            if decision != "deny" or spec.next_on_allow != END:
                raise ReportStageError(f"{name}: only a terminal stage may be skipped with deny")
            next_stage = END
            self.state.status = "skipped"
        else:
            next_stage = spec.next_on_ask if decision == "ask" and spec.next_on_ask else spec.next_on_allow
            self.state.outputs[name] = output
            if values:
                self.state.values.update(deepcopy(dict(values)))
            if next_stage == END:
                self.state.status = (
                    "partial" if decision == "ask" or any(item["decision"] == "ask" for item in self.state.transitions)
                    else "done"
                )
        transition = {"from": name, "to": next_stage, "decision": decision,
                      "reason": str((gate or {}).get("reason") or "stage output accepted")}
        self.state.transitions.append(transition)
        self.state.current_stage = next_stage
        self._active = None
        return transition
