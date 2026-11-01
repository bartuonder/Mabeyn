import json

import pytest

from mabeyn.rag import prompts
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.rag.service import Final, Retract, Token, collect
from mabeyn.seed import ingest_seed_documents, load_seed_documents
from tests.fakes import FakeLLM

QUESTION = "KKTC'ye teslimat kaç günde yapılıyor?"
ANSWER = "KKTC teslimatı 5 ile 7 iş günü sürer [Yurt Dışı ve KKTC Gönderimi:KKTC Teslimat Süresi]."


class Script:
    """Scripted replies keyed by the system prompt of each call."""

    def __init__(self, **replies):
        self.replies = {
            "rewrite": json.dumps({"query": "KKTC delivery time"}),
            "retry": json.dumps({"query": "KKTC shipping days"}),
            "grade": json.dumps({"relevant": [1, 2]}),
            "answer": ANSWER,
            "faithful": json.dumps({"faithful": True, "unsupported": []}),
            "rerank": json.dumps({"ranking": [2, 1]}),
            "fallback": "Bilgi bulunamadı.",
            **replies,
        }
        self.seen: list[str] = []

    def kind(self, system: str) -> str:
        table = {
            prompts.REWRITE_SYSTEM: "rewrite",
            prompts.REWRITE_RETRY_SYSTEM: "retry",
            prompts.GRADE_SYSTEM: "grade",
            prompts.FAITHFULNESS_SYSTEM: "faithful",
            prompts.RERANK_SYSTEM: "rerank",
            prompts.FALLBACK_SYSTEM: "fallback",
        }
        return table.get(system, "answer")

    def __call__(self, messages, model) -> str:
        kind = self.kind(messages[0]["content"])
        self.seen.append(kind)
        reply = self.replies[kind]
        if isinstance(reply, list):
            return reply.pop(0) if len(reply) > 1 else reply[0]
        return reply


@pytest.fixture
async def tenant(fresh_tenant):
    await ingest_seed_documents(fresh_tenant, load_seed_documents(), FakeLLM())
    return fresh_tenant


async def run(tenant_id, script: Script, config: RagConfig, history=()):
    llm = FakeLLM(script)
    rag = GraphRag(llm, config)
    events = [e async for e in rag.run(tenant_id, QUESTION, history, "KargoShop")]
    return events, llm


def final_of(events) -> Final:
    return next(e for e in events if isinstance(e, Final))


async def test_full_pipeline_happy_path(tenant):
    script = Script()
    events, _ = await run(tenant, script, RagConfig())
    final = final_of(events)

    assert "".join(e.text for e in events if isinstance(e, Token)).strip() == ANSWER
    assert not any(isinstance(e, Retract) for e in events)
    assert final.answer == ANSWER and not final.abstained
    assert len(final.ranked) == 6
    assert final.trace["rewrites"] == 0 and final.trace["regenerations"] == 0
    assert script.seen == ["rerank", "grade", "answer", "faithful"]


async def test_each_stage_can_be_switched_off(tenant):
    script = Script()
    config = RagConfig(retrieval="vector", rerank=False, crag=False, faithfulness=False, final_k=5)
    events, _ = await run(tenant, script, config)
    assert script.seen == ["answer"]
    assert len(final_of(events).ranked) == 5


async def test_ungraded_documents_trigger_a_rewrite_then_succeed(tenant):
    script = Script(grade=[json.dumps({"relevant": []}), json.dumps({"relevant": [1]})])
    events, _ = await run(tenant, script, RagConfig())
    final = final_of(events)
    assert script.seen.count("retry") == 1
    assert final.trace["rewrites"] == 1 and final.trace["query"] == "KKTC shipping days"
    assert not final.abstained and final.answer == ANSWER


async def test_gives_up_after_two_rewrites(tenant):
    script = Script(grade=json.dumps({"relevant": []}))
    events, _ = await run(tenant, script, RagConfig())
    final = final_of(events)
    assert script.seen.count("retry") == 2 and "answer" not in script.seen
    assert final.abstained and final.answer == "Bilgi bulunamadı." and final.citations == []
    assert any(isinstance(e, Retract) for e in events)


async def test_unfaithful_answer_is_regenerated_strictly(tenant):
    bad = json.dumps({"faithful": False, "unsupported": ["made up"]})
    good = json.dumps({"faithful": True, "unsupported": []})
    script = Script(faithful=[bad, good])
    events, llm = await run(tenant, script, RagConfig())
    final = final_of(events)

    assert final.trace["regenerations"] == 1 and not final.abstained
    assert any(isinstance(e, Retract) for e in events)
    answer_prompts = [
        m[0]["content"] for _, m in llm.chat_calls if script.kind(m[0]["content"]) == "answer"
    ]
    assert len(answer_prompts) == 2
    assert prompts.STRICT_RULE not in answer_prompts[0]
    assert prompts.STRICT_RULE in answer_prompts[1]


async def test_persistently_unfaithful_answer_becomes_an_abstention(tenant):
    script = Script(faithful=json.dumps({"faithful": False, "unsupported": ["x"]}))
    events, _ = await run(tenant, script, RagConfig())
    final = final_of(events)
    assert final.abstained and final.answer == "Bilgi bulunamadı."
    assert script.seen.count("answer") == 2


async def test_history_triggers_a_standalone_rewrite_first(tenant):
    script = Script()
    history = [{"role": "user", "content": "Merhaba"}, {"role": "assistant", "content": "Selam"}]
    events, _ = await run(tenant, script, RagConfig(), history)
    assert script.seen[0] == "rewrite"
    assert final_of(events).trace["query"] == "KKTC delivery time"


async def test_broken_grader_output_keeps_all_documents(tenant):
    script = Script(grade="not json")
    events, _ = await run(tenant, script, RagConfig())
    final = final_of(events)
    assert not final.abstained and len(final.chunks) == 6


async def test_collect_returns_the_final_event(tenant):
    llm = FakeLLM(Script())
    rag = GraphRag(llm, RagConfig(crag=False, faithfulness=False))
    final = await collect(rag.run(tenant, QUESTION, [], "KargoShop"))
    assert final.answer == ANSWER


async def test_generation_can_be_disabled_for_retrieval_experiments(tenant):
    script = Script()
    config = RagConfig(crag=False, faithfulness=False, generate=False)
    events, _ = await run(tenant, script, config)
    assert "answer" not in script.seen
    assert not any(isinstance(e, Token) for e in events)
    assert len(final_of(events).ranked) == 6


async def test_grader_sees_effective_dates_to_resolve_conflicts(tenant):
    script = Script()
    _, llm = await run(tenant, script, RagConfig())
    grade_prompt = next(
        m[1]["content"] for _, m in llm.chat_calls if script.kind(m[0]["content"]) == "grade"
    )
    assert "effective_date: 2025-" in grade_prompt
    assert "latest effective_date" in prompts.GRADE_SYSTEM
