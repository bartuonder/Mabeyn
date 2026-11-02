# Mabeyn

A secure, multi-tenant agentic support layer between businesses and their customers.

Businesses connect their documents and APIs and embed a support agent in their site. The agent answers from the documents with citations, calls tools to get work done, waits for human approval on risky actions, remembers returning customers, and is guarded by an LLM firewall.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the plan, [docs/adr](docs/adr) for design decisions and [docs/evaluation.md](docs/evaluation.md) for how every number below is produced.

## Architecture

```
upload ──> API ──> Redis queue ──> worker: parse → chunk → embed ──> PostgreSQL (pgvector, full text, RLS)
                                    worker: mine idle conversations for memories, expire old ones

customer ──> /chat (SSE) ──> supervisor graph (LangGraph, Postgres checkpointer)
              input_guard → recall_memories → supervisor ──> knowledge agent: RAG subgraph (documents only, no tools)
                                       ──> action agent: ReAct over tenant tools (never sees documents)
                                       ──> escalate: hand over to a human
              → compose → output_guard

action agent ── read / write tool ──> executes, audits in tool_calls
              └─ sensitive tool ──> Approval record + interrupt ... admin POST /approvals/{id} resumes
```

- Tenant isolation is enforced by PostgreSQL Row-Level Security ([ADR-0001](docs/adr/0001-row-level-security.md)).
- Answers cite `[document:section]`; the citation points at the section inside the retrieved chunk that holds the fact.
- The full workflow, with diagrams generated from the code, is in [docs/architecture.md](docs/architecture.md).
- Retrieval combines pgvector similarity with Turkish/English full text search (Reciprocal Rank Fusion), then an LLM reranker narrows 30 candidates to 6.
- A supervisor routes each message to a knowledge agent, an action agent or a human ([ADR-0002](docs/adr/0002-supervisor-and-two-agents.md)). Tools are registered per tenant with a JSON Schema and a risk level (`read`, `write`, `sensitive`); the platform, not the model, supplies the customer identity.
- `sensitive` tools such as refunds never run directly: the graph pauses, an admin approves or rejects, and the paused conversation survives restarts. Walk-through: [docs/demo-scenarios.md](docs/demo-scenarios.md).
- Returning customers are recognised: long conversations are summarised inside a token budget, and facts a customer states about themselves (a preferred carrier, an earlier complaint) are kept for 180 days in a store scoped to the customer. Instructions, permissions and personal data are never stored, and customers and admins can read and erase everything ([ADR-0006](docs/adr/0006-customer-memory.md)).

## Quick start

Requirements: Docker, [uv](https://docs.astral.sh/uv/), an OpenAI API key.

```bash
cp .env.example .env                 # add OPENAI_API_KEY
docker compose up -d --build         # api, worker, mock shop, postgres (pgvector), redis; migrations run first
uv sync
uv run python -m mabeyn.cli seed-kargoshop --shop-url http://kargoshop-mock:9000   # demo tenant: 32 documents, 5 tools
curl localhost:8000/health
```

Create a customer session and chat (streaming Server-Sent Events):

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email": "admin@kargoshop.example", "password": "<demo password>"}' | jq -r .access_token)
KEY=$(curl -s -X POST localhost:8000/api-keys -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"name": "widget"}' | jq -r .key)
SESSION=$(curl -s -X POST localhost:8000/sessions -H "X-API-Key: $KEY" \
  -H 'content-type: application/json' -d '{"customer_id": "customer-1"}' | jq -r .token)
curl -N -X POST localhost:8000/chat -H "Authorization: Bearer $SESSION" \
  -H 'content-type: application/json' -d '{"message": "How long does delivery to KKTC take?"}'
```

Close the conversation to let the assistant remember what was said, and look at or erase the notes:

```bash
curl -X POST localhost:8000/chat/<conversation_id>/close -H "Authorization: Bearer $SESSION"
curl localhost:8000/me/memory -H "Authorization: Bearer $SESSION"
curl -X DELETE localhost:8000/me/memory -H "Authorization: Bearer $SESSION"
```

Treat API keys as secrets: until [ADR-0005](docs/adr/0005-customer-session-trust.md) is implemented, anyone holding a key can open a session as any customer.

Admins use `GET` and `DELETE /customers/{customer_id}/memory` for the same, for example to answer a deletion request.

Documents are added with `POST /documents` (PDF, Markdown, HTML upload) or `POST /documents/url`. Tools are managed with `POST /tools` and `DELETE /tools/{id}`; pending refunds are listed with `GET /approvals?status=pending` and decided with `POST /approvals/{id}`.

## Development

```bash
uv sync
docker compose up -d postgres redis
make migrate
make test lint typecheck
```

Tests and evaluations send traces to LangSmith when `.env` enables tracing; tests switch it off themselves, and `LANGSMITH_TRACING=false` silences it for a single run (useful once the monthly trace quota of a free account is used up).

## Results

Demo tenant KargoShop: 26 Turkish and 6 English documents, including an old and a new return policy that contradict each other. 80 Turkish golden questions (16 unanswerable). Chunks of 300–600 tokens, `text-embedding-3-small`, `gpt-4.1-mini` for answers and judging, `gpt-4o-mini` for rewriting, grading, reranking and faithfulness checks. Raw runs: [`evals/results`](evals/results).

### RAG ablation (development set, 80 questions)

Each row adds one technique to the row above. Accuracy and faithfulness show a 95% interval (`evals/results/ablation.md`). This set was used while tuning, so treat the scores as optimistic.

| Configuration | Recall@1 | Recall@5 | MRR | Accuracy | Faithfulness | p95 latency (s) | Cost / query ($) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Vector only | 0.766 | 0.953 | 0.850 | 0.912 [0.83-0.96] | 0.975 [0.91-0.99] | 1.25 | 0.00082 |
| + Hybrid (full text + RRF) | 0.812 | 0.969 | 0.883 | 0.900 [0.81-0.95] | 0.963 [0.90-0.99] | 1.35 | 0.00083 |
| + Reranker (30 to 6) | 0.969 | 1.000 | 0.984 | 0.975 [0.91-0.99] | 0.988 [0.93-1.00] | 4.45 | 0.00201 |
| + CRAG grading | 0.969 | 1.000 | 0.984 | 0.975 [0.91-0.99] | 0.975 [0.91-0.99] | 9.16 | 0.00196 |
| + Faithfulness check | 0.969 | 1.000 | 0.984 | 0.963 [0.90-0.99] | 0.975 [0.91-0.99] | 8.18 | 0.00204 |

Measured with prompt `rag-v5` (every row was rerun after the language fix).

- Ranking quality improves clearly with hybrid search and the reranker (Recall@1 0.77 to 0.97, MRR 0.85 to 0.98).
- Accuracy rises from about 0.91 to 0.975 with the reranker; the intervals of neighbouring rows overlap and the set was used for tuning. CRAG grading and the faithfulness check show no measurable effect on this set (an earlier run with prompt `rag-v4` had CRAG at 0.975 versus 0.938 for the reranker alone; the same configurations now differ by noise). Their value shows on unanswerable questions in the held-out set below.
- Latency is the RAG subgraph only and varies between runs. Measured end to end on the knowledge path the first streamed token arrives after about 4.1 s (median), the whole answer after about 5.6 s, because routing, query handling, reranking and grading run first.
- On an earlier run, the same vector-only answers judged by `gpt-4.1` instead of `gpt-4.1-mini` scored 0.925 instead of 0.900 (two verdicts differ), so the judge model moves the numbers by a couple of points.
- English set (20 questions, 17 answerable), measured earlier with the full pipeline: Recall@5 1.00, accuracy 0.95.

### Held-out set (30 fresh questions, judge `gpt-4.1`)

Written after the first held-out set had been consumed by a fix, with gold sections no other set uses, and measured once (`evals/results/heldout2.md`). 25 questions are answerable (21 Turkish, 3 English over English documents, 1 English over a Turkish document) and 5 are not.

| Configuration | Recall@1 | MRR | Accuracy | Answerable | Unanswerable (5) | Faithfulness | p95 latency (s) | Cost / query ($) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Vector only | 0.720 | 0.830 | 0.933 [0.79-0.98] | 0.960 | 0.800 | 1.000 [0.89-1.00] | 2.45 | 0.00077 |
| Hybrid + reranker | 0.960 | 0.980 | 0.933 [0.79-0.98] | 1.000 | 0.600 | 0.933 [0.79-0.98] | 3.44 | 0.00199 |
| Full pipeline | 0.960 | 0.980 | 0.967 [0.83-0.99] | 1.000 | 0.800 | 1.000 [0.89-1.00] | 9.63 | 0.00200 |

- The reranker again lifts ranking (Recall@1 0.72 to 0.96) and answers every answerable question; the full pipeline gives the best overall accuracy, but 28, 28 and 29 correct answers out of 30 cannot be told apart statistically.
- The one visible difference is on unanswerable questions: with only five of them, hybrid plus reranker invents an answer for two, the full pipeline for one. Treat this as a hint that grading and the faithfulness check guard against invented answers, not as proof.
- The first held-out set (30 questions, all configurations 1.000) is consumed and kept only as a regression test; it was too easy to separate the configurations.

### Experiments (retrieval only, 80 questions)

Chunk size (hybrid search):

| Chunks (min–max tokens) | Recall@1 | Recall@5 | MRR |
| --- | ---: | ---: | ---: |
| fine (80–160) | 0.734 | 0.969 | 0.842 |
| medium (150–300) | 0.781 | 0.984 | 0.863 |
| default (300–600) | 0.812 | 0.969 | 0.883 |
| coarse (500–900) | 0.766 | 0.969 | 0.859 |

Embedding model and reranker (default chunks):

| Configuration | Recall@1 | Recall@5 | MRR |
| --- | ---: | ---: | ---: |
| text-embedding-3-small, vector | 0.766 | 0.953 | 0.852 |
| text-embedding-3-small, hybrid | 0.812 | 0.969 | 0.883 |
| text-embedding-3-large, vector | 0.812 | 0.969 | 0.882 |
| text-embedding-3-large, hybrid | 0.859 | 0.984 | 0.912 |
| hybrid, reranker off | 0.812 | 0.969 | 0.883 |
| hybrid, reranker on | 0.969 | 1.000 | 0.984 |

The corpus is small, so Recall@5 saturates; Recall@1 and MRR separate the configurations. The larger embedding model gains little once the reranker is on, so the smaller one stays the default.

### Judge calibration

On 20 sampled answers the LLM judge agrees with the manual labels on 20 of 20 (Cohen's kappa 1.0). All labels were reviewed by the maintainer (`evals/datasets/judge_calibration.jsonl`). Only two judged-incorrect answers existed to sample, so the figure says more about the correct verdicts than about the judge's ability to catch errors.

### Tool use and approvals

Scenarios on the supervisor with the mock shop ([how they are scored](docs/evaluation.md#tool-use-evaluation)). The development set (40) was used for tuning. The first held-out set (15) is consumed: its language failure led to a fix, so its column predates that fix and is kept for reference. The fresh held-out set (15, judge `gpt-4.1`) was written afterwards and measured once.

| Metric | Development (40) | Held-out, consumed (15) | Held-out, fresh (15) |
| --- | ---: | ---: | ---: |
| Scenarios passed | 0.975 [0.87-1.00] | 0.867 [0.62-0.96] | 0.867 [0.62-0.96] |
| Correct tool selection | 1.000 | 1.000 | 0.933 |
| Correct arguments | 1.000 | 1.000 | 0.833 |
| Unnecessary tool calls | 0.000 | 0.000 | 0.000 |
| Sensitive calls that reached approval | 1.000 | 1.000 | 1.000 |
| Sensitive actions executed without approval | 0 | 0 | 0 |
| Routing accuracy | 1.000 | 1.000 | 1.000 |
| Reply judged correct | 0.975 | 0.933 | 0.933 |
| Reply in the customer's language | 1.000 | 0.933 | 1.000 |
| Other customer's data in a reply | 0 | 0 | 0 |
| Cost per scenario | $0.002 | $0.002 | $0.002 |

- The approval gate, routing and tenant/customer isolation held on every set. The approval rate is 100% by construction (the graph routes every `sensitive` call to approval); the real risk is the model not calling a tool, which tool selection measures.
- The language fix works: both English knowledge questions in the development and fresh sets are answered in English (language accuracy 1.000).
- The two failures in the fresh set are scoring artefacts rather than wrong replies, and the set was not changed afterwards. In one the assistant listed the customer's orders instead of calling the status tool for an order number given in the message, and answered correctly (the scenario required the status tool). In the other the judge marked a correct delivery reply wrong because it did not repeat the order number.
- The development set has one known failure: an English question that finds nothing in Turkish documents (cross-lingual retrieval is weak).

### Memory

Two-session scenarios ([how they are scored](docs/evaluation.md#memory-evaluation)): in session 1 a customer states something and the conversation is mined for memories, in session 2 a new conversation asks about it. The development set (15) was used for tuning; the held-out set (12, judge `gpt-4.1`) was written first and measured once.

| Metric | Development (15) | Held-out (12) |
| --- | ---: | ---: |
| Scenarios passed | 1.000 [0.80-1.00] | 0.917 [0.65-0.99] |
| Correct recall in session 2 | 1.000 (9 of 9) [0.70-1.00] | 1.000 (6 of 6) [0.61-1.00] |
| Expected note stored after session 1 | 1.000 | 1.000 |
| Fabricated recall (nothing was said, or another customer said it) | 0.000 (0 of 2) | 0.000 (0 of 2) |
| Other customer's note in an answer | 0 | 0 |
| Poisoned or personal data stored | 0 of 4 | 0 of 4 |
| Poisoned scenarios whose refund waited for approval | 3 of 3 | 2 of 3 |
| Refunds executed without approval | 0 | 0 |
| Cost per scenario | $0.002 | $0.002 |

- Recall, updates ("switch me to MNG"), missing facts and customer separation held on both sets, but 6 to 9 scenarios per metric cannot separate 0.90 from 1.00; read the intervals.
- No instruction, entitlement claim, card number, IBAN, e-mail address or phone number reached the store. The extraction prompt already dropped every poisoned statement before the deterministic screen saw it, so the screen itself is covered by unit tests, not by this table.
- The one held-out miss is not a stored poison: in a Turkish refund request the action agent explained the refund steps instead of calling `request_refund`, so nothing reached the approval queue (and nothing was executed). The same request passes without notes in the tool-use set, so the notes block seems to change the agent's behaviour; this was not investigated further. The set was not changed for it, and it stays an open defect.
- Development-set work found two product defects, now fixed: slugs with Turkish letters made the schema drop valid facts, and a preference phrased as a request ("from now on") was mistaken for an instruction. It also found an evaluation defect: the runner wrote both session-1 messages in one transaction, so their order was undefined; it now writes one transaction per message like real chat turns. Fixed slugs for singular facts (carrier, region) were added so a newer statement replaces an older one.

## Status

- Phase 1 — foundation, tenant isolation, auth: done
- Phase 2 — ingestion and RAG v1: done
- Phase 3 — RAG v2 and evaluation: done
- Phase 4 — agentic layer, tools, human approval: done
- Phase 5 — memory: done
- Phases 6–8 — firewall, LLMOps, product polish: planned, see the roadmap
