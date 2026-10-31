# Roadmap

Eight phases. Each ends with a working build and a measured result.
Eval, red-team and tenant isolation are never cut.

## Phase 1 — Foundation and tenant isolation
- uv project, ruff, mypy, pytest, pre-commit
- Docker Compose: api, postgres (pgvector), redis
- FastAPI skeleton, pydantic-settings, structured logging, `/health`
- SQLAlchemy 2.0 async + Alembic
- GitHub Actions: lint, types, tests
- Data model with `tenant_id` everywhere, PostgreSQL Row-Level Security
- Admin auth (JWT + refresh rotation, argon2), tenant API keys, short-lived customer tokens
- **Done when:** `/health` returns 200, CI is green, isolation tests pass, ADR-001 written

## Phase 2 — Ingestion and RAG v1
- Upload (PDF, Markdown, HTML, URL), arq worker, `pending → processing → ready | failed`
- Heading-aware chunking (300–600 tokens) with context headers and metadata
- Embeddings + pgvector HNSW
- Tenant-filtered retrieval, cited answers, SSE streaming on `/chat`
- Demo tenant KargoShop: 25–30 documents, two contradicting policies
- Eval v0: 50 questions, Recall@5, MRR, accuracy, faithfulness
- **Done when:** cited streaming answers work and baseline scores are stored

## Phase 3 — RAG v2 and evals
- Hybrid search (Turkish FTS + vectors, RRF)
- Cross-encoder reranker (30 → 6)
- LangGraph RAG subgraph: rewrite → retrieve → grade (CRAG) → generate → faithfulness check
- Experiment tracking, LangSmith traces, eval set grown to 80
- **Done when:** ablation table has 5+ rows (Recall@5, accuracy, faithfulness, p95, cost/query)

## Phase 4 — Agentic layer
- Mock KargoShop API, tool registry (JSON Schema → Pydantic), risk levels, HTTP executor, audit log
- Action agent (ReAct, max 5 calls), knowledge agent, supervisor, escalation
- Human approval via LangGraph interrupt, Postgres checkpointer
- Tool-use eval: 40 scenarios
- **Done when:** four demo scenarios run end to end, ADR-002 written

## Phase 5 — Memory
- Short-term: checkpointer threads, summarize and trim
- Long-term: LangGraph Store, `(tenant_id, customer_id)` namespace
- `extract_memories` / `recall_memories`, memory hygiene, TTL, delete endpoint
- Memory eval: 15 two-session scenarios
- **Done when:** preferences recalled, poisoned instructions rejected, deletion works

## Phase 6 — LLM firewall and red-team
- Input rules + injection classifier, indirect injection handling, spotlighting
- Tool guards: allowlists, argument policies, rate limits
- Output PII masking, canary token, `SecurityEvent` logging
- Red-team set: 80 attacks + 80 benign
- **Done when:** ASR / false-block table ready, ADR-003 written

## Phase 7 — LLMOps, cache, performance
- Exact + semantic cache, sliding-window rate limiting
- Prompt versioning, cost tracking, usage endpoint
- CI eval gate, Locust load test
- **Done when:** cache, latency and cost tables ready; gate caught a real regression

## Phase 8 — Productization
- Admin panel, embeddable chat widget
- `make demo` seeds everything
- README with architecture diagram, demo GIF and result tables
- Error analysis, demo video, blog post
- **Done when:** `git clone && make demo` runs in 5 minutes, every README number links to an eval result
