import json

import pytest

from evals.tool_scoring import (
    ExpectedCall,
    ObservedCall,
    Scenario,
    load_scenarios,
    route_matches,
    score_scenario,
    summarize_scores,
)
from mabeyn.seed import load_tool_definitions


def scenario(**overrides) -> Scenario:
    base = dict(
        id="t",
        category="c",
        customer_id="customer-1",
        message="m",
        route="action",
        required=(ExpectedCall("get_order_status", {"order_id": "KS-1001"}),),
        allowed=frozenset({"get_order_status"}),
        forbidden=frozenset({"request_refund"}),
        status="answered",
        approval=False,
        absent_in_answer=(),
    )
    return Scenario(**{**base, **overrides})


def call(name="get_order_status", status="success", **arguments) -> ObservedCall:
    return ObservedCall(name, arguments or {"order_id": "KS-1001"}, status)


def test_a_clean_scenario_passes():
    score = score_scenario(scenario(), [call()], ["action"], "answered", "ok")
    assert score.passed and score.args_ok is True and score.unnecessary_calls == 0


def test_missing_and_forbidden_calls_fail_tool_selection():
    missing = score_scenario(scenario(), [], ["action"], "answered", "")
    assert not missing.tool_ok and not missing.passed
    forbidden = score_scenario(
        scenario(), [call(), call("request_refund", order_id="KS-1001")], ["action"], "answered", ""
    )
    assert not forbidden.tool_ok and forbidden.unnecessary_calls == 1


def test_wrong_arguments_are_detected_but_numbers_and_accents_are_tolerant():
    wrong = score_scenario(scenario(), [call(order_id="KS-9999")], ["action"], "answered", "")
    assert wrong.tool_ok and wrong.args_ok is False

    refund = scenario(
        required=(ExpectedCall("request_refund", {"amount": 899.9, "city": "Izmir"}),),
        allowed=frozenset({"request_refund"}),
        forbidden=frozenset(),
        status="pending_approval",
        approval=True,
    )
    observed = ObservedCall(
        "request_refund", {"amount": 899.90001, "city": "İzmir"}, "pending_approval"
    )
    assert score_scenario(refund, [observed], ["action"], "pending_approval", "").args_ok is True


def test_calls_outside_the_allowed_set_count_as_unnecessary():
    score = score_scenario(
        scenario(forbidden=frozenset()),
        [call(), call("list_orders"), call("list_orders")],
        ["action"],
        "answered",
        "",
    )
    assert score.unnecessary_calls == 2 and not score.passed


def test_approval_expectations_work_in_both_directions():
    needs = scenario(approval=True, status="pending_approval")
    pending = ObservedCall("request_refund", {}, "pending_approval")
    assert score_scenario(needs, [pending], ["action"], "pending_approval", "").approval_ok
    assert not score_scenario(needs, [], ["action"], "answered", "").approval_ok
    assert not score_scenario(scenario(), [pending], ["action"], "answered", "").approval_ok


def test_leaked_customer_data_fails_the_scenario():
    guarded = scenario(absent_in_answer=("Smart Watch",))
    score = score_scenario(guarded, [call()], ["action"], "answered", "Your smart watch ships soon")
    assert score.leaked and not score.passed


@pytest.mark.parametrize(
    ("expected", "plan", "ok"),
    [
        ("knowledge", ["knowledge"], True),
        ("knowledge", ["action"], False),
        ("both", ["knowledge", "action"], True),
        ("both", ["action"], True),
        ("both", ["knowledge"], False),
        ("escalate", ["escalate"], True),
    ],
)
def test_route_matching(expected, plan, ok):
    assert route_matches(expected, plan) is ok


def test_summary_rates():
    good = score_scenario(scenario(), [call()], ["action"], "answered", "")
    bad = score_scenario(scenario(), [call(order_id="KS-0")], ["action"], "answered", "")
    good.sensitive_calls, good.sensitive_with_approval = 2, 2
    metrics = summarize_scores([good, bad])
    assert metrics["scenarios"] == 2.0 and metrics["scenario_pass_rate"] == 0.5
    assert metrics["argument_accuracy"] == 0.5 and metrics["sensitive_approval_rate"] == 1.0
    assert metrics["unnecessary_call_rate"] == 0.0


def test_dataset_is_consistent_with_the_seed_tools_and_orders():
    scenarios = load_scenarios()
    tools = {d["name"]: d for d in load_tool_definitions("http://shop")}
    orders = json.loads(
        (__import__("pathlib").Path("seed/kargoshop/orders.json")).read_text(encoding="utf-8")
    )["orders"]
    owner = {o["id"]: o["customer_id"] for o in orders}

    assert len(scenarios) == 40 and len({s.id for s in scenarios}) == 40
    assert sum(s.approval for s in scenarios) >= 4
    for s in scenarios:
        for c in s.required:
            assert c.name in tools, s.id
            for key in c.arguments:
                assert key in tools[c.name]["json_schema"]["properties"], (s.id, key)
        assert s.forbidden.isdisjoint({c.name for c in s.required}), s.id
        assert {c.name for c in s.required} <= s.allowed or not s.required, s.id
        assert {*s.allowed, *s.forbidden} <= set(tools), s.id
        assert s.customer_id in set(owner.values())
        if s.approval:
            assert s.status == "pending_approval" and s.id
    own_orders = [
        (s, c.arguments["order_id"])
        for s in scenarios
        for c in s.required
        if "order_id" in c.arguments and s.category != "identity"
    ]
    assert all(owner[order] == s.customer_id for s, order in own_orders)


def test_outcome_verdicts_feed_the_score():
    from evals.tool_scoring import OutcomeVerdict

    good = OutcomeVerdict(True, "ok")
    assert score_scenario(scenario(), [call()], ["action"], "answered", "", verdict=good).passed
    wrong = OutcomeVerdict(False, "claimed a refund")
    scored = score_scenario(scenario(), [call()], ["action"], "answered", "", verdict=wrong)
    assert scored.outcome_ok is False and not scored.passed
    both = [
        score_scenario(scenario(), [call()], ["action"], "answered", "", verdict=v)
        for v in (good, wrong)
    ]
    assert summarize_scores(both)["outcome_accuracy"] == 0.5


def test_language_is_checked_without_a_model():
    from evals.tool_scoring import guess_language, language_matches

    assert guess_language("Where is my order, and can you help me?") == "en"
    assert guess_language("Siparişim nerede, kargoya verildi mi?") == "tr"
    assert guess_language("Hello!") is None
    assert guess_language("[İade Politikası:İade Süresi]") is None
    assert language_matches("Where is order KS-1?", "Your order has shipped.") is True
    assert language_matches("Where is order KS-1?", "Siparişiniz kargoya verilmiş.") is False
    assert language_matches("Hello!", "Merhaba, size nasıl yardımcı olabilirim?") is None

    wrong_language = score_scenario(
        scenario(message="Where is order KS-1001?"), [call()], ["action"], "answered",
        "Siparişiniz kargoya verildi ve bu ürün için teslim edilecektir.",
    )  # fmt: skip
    assert wrong_language.language_ok is False and not wrong_language.passed


def test_every_scenario_states_its_expected_outcome():
    assert all(s.outcome.strip() for s in load_scenarios())


def test_heldout_scenarios_are_valid_and_do_not_repeat_development_ones():
    heldout = load_scenarios("tools_heldout.jsonl")
    dev_messages = {s.message.casefold() for s in load_scenarios()}
    tools = {d["name"]: d for d in load_tool_definitions("http://shop")}

    assert len(heldout) == 15 and len({s.id for s in heldout}) == 15
    assert not dev_messages & {s.message.casefold() for s in heldout}
    assert sum(bool(s.history) for s in heldout) >= 2
    for s in heldout:
        assert s.outcome.strip(), s.id
        assert {c.name for c in s.required} <= set(tools), s.id
        assert {*s.allowed, *s.forbidden} <= set(tools), s.id
        for turn in s.history:
            assert turn["role"] in {"user", "assistant"} and turn["content"].strip()
