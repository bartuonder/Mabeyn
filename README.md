# Mabeyn

A secure, multi-tenant agentic support layer between businesses and their customers.

Businesses connect their documents and APIs and embed a support agent in their site. The agent answers from the documents with citations, calls tools to get work done, waits for human approval on risky actions, remembers returning customers, and is guarded by an LLM firewall.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the plan, [docs/adr](docs/adr) for design decisions and [docs/evaluation.md](docs/evaluation.md) for how every number below is produced.

## Architecture

```
upload ──> API ──> Redis queue ──> worker: parse → chunk → embed ──> PostgreSQL (pgvector, full text, RLS)

customer ──> /chat (SSE) ──> supervisor graph (LangGraph, Postgres checkpointer)
              input_guard → supervisor ──> knowledge agent: RAG subgraph (documents only, no tools)
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

Treat API keys as secrets: until [ADR-0005](docs/adr/0005-customer-session-trust.md) is implemented, anyone holding a key can open a session as any customer.

Documents are added with `POST /documents` (PDF, Markdown, HTML upload) or `POST /documents/url`. Tools are managed with `POST /tools` and `DELETE /tools/{id}`; pending refunds are listed with `GET /approvals?status=pending` and decided with `POST /approvals/{id}`.

## Development

```bash
uv sync
docker compose up -d postgres redis
make migrate
make test lint typecheck
```

## Results

Demo tenant KargoShop: 26 Turkish and 6 English documents, including an old and a new return policy that contradict each other. 80 Turkish golden questions (16 unanswerable). Chunks of 300–600 tokens, `text-embedding-3-small`, `gpt-4.1-mini` for answers and judging, `gpt-4o-mini` for rewriting, grading, reranking and faithfulness checks. Raw runs: [`evals/results`](evals/results).

### RAG ablation (development set, 80 questions)

Each row adds one technique to the row above. Accuracy and faithfulness show a 95% interval (`evals/results/ablation.md`). This set was used while tuning, so treat the scores as optimistic.

| Configuration | Recall@1 | Recall@5 | MRR | Accuracy | Faithfulness | p95 latency (s) | Cost / query ($) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Vector only | 0.766 | 0.953 | 0.850 | 0.900 [0.81-0.95] | 0.975 [0.91-0.99] | 1.20 | 0.00081 |
| + Hybrid (full text + RRF) | 0.812 | 0.969 | 0.883 | 0.912 [0.83-0.96] | 0.963 [0.90-0.99] | 1.36 | 0.00082 |
| + Reranker (30 to 6) | 0.969 | 1.000 | 0.984 | 0.938 [0.86-0.97] | 0.975 [0.91-0.99] | 3.97 | 0.00200 |
| + CRAG grading | 0.969 | 1.000 | 0.984 | 0.975 [0.91-0.99] | 0.988 [0.93-1.00] | 7.29 | 0.00195 |
| + Faithfulness check | 0.969 | 1.000 | 0.984 | 0.975 [0.91-0.99] | 0.988 [0.93-1.00] | 7.30 | 0.00203 |

- Ranking quality improves clearly with hybrid search and the reranker (Recall@1 0.77 to 0.97, MRR 0.85 to 0.98).
- Accuracy rises from 0.90 to 0.975, but neighbouring rows overlap and the set was used for tuning; the CRAG step in particular is a hint, not a proof (see the held-out set below).
- The faithfulness check shows no measurable effect; it costs about $0.00008 per query.
- Latency is the RAG subgraph only. Measured end to end on the knowledge path the first streamed token arrives after about 4.1 s (median), the whole answer after about 5.6 s, because routing, query handling, reranking and grading run first.
- The same vector-only answers judged by `gpt-4.1` instead of `gpt-4.1-mini` score 0.925 instead of 0.900 (two verdicts differ), so the judge model moves the numbers by a couple of points.
- English set (20 questions, 17 answerable), measured earlier with the full pipeline: Recall@5 1.00, accuracy 0.95.

### Held-out set (30 questions, judge `gpt-4.1`)

Written before the final measurement and never used for tuning (`evals/results/heldout.md`).

| Configuration | Recall@1 | MRR | Accuracy | Faithfulness | p95 latency (s) | Cost / query ($) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Vector only | 0.917 | 0.948 | 1.000 [0.89-1.00] | 0.967 [0.83-0.99] | 1.77 | 0.00085 |
| Hybrid + reranker | 1.000 | 1.000 | 1.000 [0.89-1.00] | 0.967 [0.83-0.99] | 3.47 | 0.00205 |
| Full pipeline | 1.000 | 1.000 | 1.000 [0.89-1.00] | 0.967 [0.83-0.99] | 8.78 | 0.00204 |

This set is easier than the development set: even vector-only answers all 30 questions correctly (including 2 questions asked in English over Turkish documents and 6 unanswerable ones). It confirms the ranking gain of the reranker but cannot confirm or refute the accuracy gain of CRAG, so that claim rests on the development set only.

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

Scenarios on the supervisor with the mock shop ([how they are scored](docs/evaluation.md#tool-use-evaluation)). The development set (40) was used for tuning; the held-out set (15, judge `gpt-4.1`) was not.

| Metric | Development (40) | Held-out (15) |
| --- | ---: | ---: |
| Scenarios passed | 0.975 [0.87-1.00] | 0.867 [0.62-0.96] |
| Correct tool selection | 1.000 | 1.000 |
| Correct arguments | 1.000 | 1.000 |
| Unnecessary tool calls | 0.000 | 0.000 |
| Sensitive calls that reached approval | 1.000 | 1.000 |
| Sensitive actions executed without approval | 0 | 0 |
| Routing accuracy | 1.000 | 1.000 |
| Reply judged correct | 0.975 | 0.933 |
| Reply in the customer's language | 1.000 | 0.933 |
| Other customer's data in a reply | 0 | 0 |
| Cost per scenario | $0.002 | $0.002 |

- Tool choice, arguments and the approval gate hold on unseen scenarios, including two multi-turn ones. The approval rate is 100% by construction (the graph routes every `sensitive` call to approval); the real risk is the model not calling a tool, which tool selection measures.
- Open defects found by the held-out set: an English question answered over Turkish documents is answered in Turkish when it takes the knowledge-only path (the supervisor knows the language but the RAG answer prompt is not told), and one scenario failed because the judge insisted on the word "delivered". The development set has one known failure: an English question that finds nothing in Turkish documents.

## Status

- Phase 1 — foundation, tenant isolation, auth: done
- Phase 2 — ingestion and RAG v1: done
- Phase 3 — RAG v2 and evaluation: done
- Phase 4 — agentic layer, tools, human approval: done
- Phases 5–8 — memory, firewall, LLMOps, product polish: planned, see the roadmap
