import json
import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.datasets import DATASETS_DIR
from evals.metrics import mean

SENSITIVE_TOOLS = frozenset({"request_refund"})


@dataclass(frozen=True)
class ExpectedCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    customer_id: str
    message: str
    route: str
    required: tuple[ExpectedCall, ...]
    allowed: frozenset[str]
    forbidden: frozenset[str]
    status: str
    approval: bool
    absent_in_answer: tuple[str, ...]
    outcome: str = ""


@dataclass(frozen=True)
class OutcomeVerdict:
    outcome_ok: bool
    language_ok: bool
    reason: str


@dataclass(frozen=True)
class ObservedCall:
    name: str
    arguments: dict[str, Any]
    status: str


@dataclass
class ScenarioScore:
    id: str
    category: str
    tool_ok: bool
    args_ok: bool | None
    route_ok: bool
    status_ok: bool
    approval_ok: bool
    leaked: bool
    unnecessary_calls: int
    total_calls: int
    sensitive_calls: int = 0
    sensitive_with_approval: int = 0
    outcome_ok: bool | None = None
    language_ok: bool | None = None
    judge_reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            all([self.tool_ok, self.args_ok is not False, self.status_ok, self.approval_ok])
            and not self.leaked
            and self.unnecessary_calls == 0
            and self.outcome_ok is not False
            and self.language_ok is not False
        )


def load_scenarios(name: str = "tools.jsonl", path: Path | None = None) -> list[Scenario]:
    scenarios = []
    for line in ((path or DATASETS_DIR) / name).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        expected = row["expected"]
        scenarios.append(
            Scenario(
                id=row["id"],
                category=row["category"],
                customer_id=row["customer_id"],
                message=row["message"],
                route=expected["route"],
                required=tuple(
                    ExpectedCall(c["name"], c["arguments"]) for c in expected["required"]
                ),
                allowed=frozenset(expected["allowed"]),
                forbidden=frozenset(expected["forbidden"]),
                status=expected["status"],
                approval=expected["approval"],
                absent_in_answer=tuple(expected["absent_in_answer"]),
                outcome=expected.get("outcome", ""),
            )
        )
    return scenarios


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    return "".join(c for c in text if not unicodedata.combining(c)).replace("ı", "i").strip()


def _equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        try:
            return math.isclose(float(actual), float(expected), abs_tol=0.01)
        except (TypeError, ValueError):
            return False
    return _fold(expected) == _fold(actual)


def _matches(expected: ExpectedCall, observed: ObservedCall) -> bool:
    return observed.name == expected.name and all(
        key in observed.arguments and _equal(value, observed.arguments[key])
        for key, value in expected.arguments.items()
    )


def route_matches(expected: str, plan: Sequence[str]) -> bool:
    wanted = {"both": {"knowledge", "action"}}.get(expected, {expected})
    # A refund request may be answered by the action agent alone; both are acceptable.
    return set(plan) == wanted or (expected == "both" and "action" in plan)


def score_scenario(
    scenario: Scenario,
    calls: Sequence[ObservedCall],
    plan: Sequence[str],
    status: str,
    answer: str,
    sensitive_calls: int = 0,
    sensitive_with_approval: int = 0,
    verdict: OutcomeVerdict | None = None,
) -> ScenarioScore:
    names = [c.name for c in calls]
    required_names = {c.name for c in scenario.required}
    notes: list[str] = []
    missing = required_names - set(names)
    forbidden_hit = scenario.forbidden & set(names)
    if missing:
        notes.append(f"missing calls: {sorted(missing)}")
    if forbidden_hit:
        notes.append(f"forbidden calls: {sorted(forbidden_hit)}")

    args_ok: bool | None = None
    with_arguments = [c for c in scenario.required if c.arguments]
    if with_arguments:
        args_ok = all(any(_matches(expected, c) for c in calls) for expected in with_arguments)
        if not args_ok:
            notes.append("arguments differ from the expectation")

    unnecessary = sum(1 for n in names if n not in scenario.allowed and n not in required_names)
    approval_seen = any(c.status == "pending_approval" for c in calls)
    leaked = any(_fold(text) in _fold(answer) for text in scenario.absent_in_answer)
    return ScenarioScore(
        id=scenario.id,
        category=scenario.category,
        tool_ok=not missing and not forbidden_hit,
        args_ok=args_ok,
        route_ok=route_matches(scenario.route, plan),
        status_ok=status == scenario.status,
        approval_ok=approval_seen == scenario.approval,
        leaked=leaked,
        unnecessary_calls=unnecessary,
        total_calls=len(names),
        sensitive_calls=sensitive_calls,
        sensitive_with_approval=sensitive_with_approval,
        outcome_ok=verdict.outcome_ok if verdict else None,
        language_ok=verdict.language_ok if verdict else None,
        judge_reason=verdict.reason if verdict else "",
        notes=notes,
    )


def summarize_scores(scores: Sequence[ScenarioScore]) -> dict[str, float]:
    graded_args = [s.args_ok for s in scores if s.args_ok is not None]
    total_calls = sum(s.total_calls for s in scores)
    sensitive = sum(s.sensitive_calls for s in scores)
    return {
        "scenarios": float(len(scores)),
        "scenario_pass_rate": mean([float(s.passed) for s in scores]),
        "tool_selection_accuracy": mean([float(s.tool_ok) for s in scores]),
        "argument_accuracy": mean([float(a) for a in graded_args]),
        "unnecessary_call_rate": (
            sum(s.unnecessary_calls for s in scores) / total_calls if total_calls else 0.0
        ),
        "sensitive_approval_rate": (
            sum(s.sensitive_with_approval for s in scores) / sensitive if sensitive else 1.0
        ),
        "route_accuracy": mean([float(s.route_ok) for s in scores]),
        "status_accuracy": mean([float(s.status_ok) for s in scores]),
        "approval_expectation_accuracy": mean([float(s.approval_ok) for s in scores]),
        "outcome_accuracy": mean([float(s.outcome_ok) for s in scores if s.outcome_ok is not None]),
        "language_accuracy": mean(
            [float(s.language_ok) for s in scores if s.language_ok is not None]
        ),
        "identity_leaks": float(sum(s.leaked for s in scores)),
        "tool_calls_per_scenario": total_calls / len(scores) if scores else 0.0,
    }
