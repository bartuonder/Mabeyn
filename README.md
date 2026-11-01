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

### RAG ablation

Each row adds one technique to the row above (`evals/results/ablation.md`).

| Configuration | Recall@1 | Recall@5 | MRR | Accuracy | Faithfulness | p95 latency (s) | Cost / query ($) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Vector only | 0.766 | 0.953 | 0.850 | 0.900 | 0.975 | 1.20 | 0.00081 |
| + Hybrid (full text + RRF) | 0.812 | 0.969 | 0.883 | 0.912 | 0.963 | 1.20 | 0.00082 |
| + Reranker (30 → 6) | 0.969 | 1.000 | 0.984 | 0.950 | 0.988 | 2.84 | 0.00200 |
| + CRAG grading | 0.969 | 1.000 | 0.984 | 0.975 | 0.975 | 7.52 | 0.00195 |
| + Faithfulness check | 0.969 | 1.000 | 0.984 | 0.975 | 0.975 | 7.08 | 0.00203 |

- Hybrid search fixes the questions where vector search misses Turkish suffixed terms ("KKTC'ye", "Almanya'ya").
- The reranker is the largest single gain in ranking quality; the grader then removes the superseded 2023 return policy and answers every answerable question correctly (accuracy 1.00 on answerable questions, 0.875 on unanswerable ones).
- CRAG raises p95 latency because unanswerable questions go through two query rewrites before the assistant declines.
- The faithfulness check showed no measurable change on this set; it stays as a guard because it costs about $0.00008 per query.
- English set (20 questions, 17 answerable): Recall@5 1.00, accuracy 0.95 with the full pipeline.

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

40 scenarios on the supervisor with the mock shop (`evals/datasets/tools.jsonl`, [how it is scored](docs/evaluation.md#tool-use-evaluation)). The reply language is checked with a heuristic and the reply content by an LLM judge.

| Metric | Result |
| --- | ---: |
| Scenarios passed | 0.975 |
| Correct tool selection | 1.000 |
| Correct arguments | 1.000 |
| Unnecessary tool calls | 0.000 |
| Sensitive calls that reached approval | 1.000 |
| Sensitive actions executed without approval | 0 |
| Routing accuracy | 1.000 |
| Replies in the customer's language | 1.000 |
| Other customer's data in a reply | 0 |
| Cost per scenario | $0.002 |

The one failing scenario is an English question asked against Turkish-only documents ("Do you ship to Germany?"); retrieval across languages is not handled yet.

## Status

- Phase 1 — foundation, tenant isolation, auth: done
- Phase 2 — ingestion and RAG v1: done
- Phase 3 — RAG v2 and evaluation: done
- Phase 4 — agentic layer, tools, human approval: done
- Phases 5–8 — memory, firewall, LLMOps, product polish: planned, see the roadmap
