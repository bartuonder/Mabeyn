# Roadmap

Eight phases. Each ends with a working build and a measured result.
Tenant isolation, evals and red-teaming are never cut.
Do not start a phase before the previous one meets its definition of done.

## Status

| Phase | State | Notes |
| --- | --- | --- |
| 1 Foundation and tenant isolation | done | ADR-0001 |
| 2 Ingestion and RAG v1 | done | |
| 3 RAG v2 and evals | done | Ablation, experiments and a held-out set; see [evaluation.md](evaluation.md) |
| 4 Agentic layer | done | ADR-0002, [architecture.md](architecture.md) |
| 5 Memory | planned | |
| 6 LLM firewall and red-team | planned | ADR-0003 reserved; also closes the open items in [ADR-0005](adr/0005-customer-session-trust.md) |
| 7 LLMOps, cache, performance | planned | |
| 8 Productization | planned | Widget and session keys follow ADR-0005 |

Deliberate differences from the plan below:
- Embeddings come from the OpenAI API instead of a local bge-m3, and the reranker is an LLM listwise reranker instead of a cross-encoder.
- Gold references in the evaluation sets point to a document and a section heading instead of chunk ids, so chunk size experiments reuse the same questions ([ADR-0004](adr/0004-evaluation-driven-rag.md)).
- The demo documents and the golden set are Turkish, with a small English set.
- The plan's phases 0 and 1 are one phase here, and weeks are not tracked.
- Evaluation results are reported with 95% confidence intervals, and a held-out set that is never used for tuning backs the development numbers.

## Phase 1 — Foundation and tenant isolation

Goal: a one-command stack with green CI, and a proof (by test) that tenant A can never see tenant B's data. The guarantee lives in the database, not in application code.

Foundation
- uv project on Python 3.12, ruff, mypy, pytest, pre-commit
- Docker Compose: api, postgres (pgvector image), redis
- FastAPI skeleton: pydantic-settings, structured logging, `/health`
- SQLAlchemy 2.0 (async) + Alembic, first migration
- GitHub Actions: lint, type check, tests
- Makefile: `make up`, `make test`, `make migrate`
- `docs/adr/`: one short ADR per significant decision

Tenant core
- Models: `Tenant`, `AdminUser`, `ApiKey`, `Document`, `Chunk`, `Conversation`, `Message`, `ToolDefinition`, `ToolCall` (audit), `Approval`, `SecurityEvent`
- `tenant_id` on every table; PostgreSQL Row-Level Security policies; `SET app.tenant_id` at the start of each request
- Admin auth: JWT access + refresh tokens, argon2 password hashing, refresh token rotation
- Tenant API keys: stored hashed, used by the embedded widget
- End-customer session: short-lived (about 15 min) signed token obtained with an API key, carrying `tenant_id` and `customer_id`
- Pydantic request/response schemas with strict validation (`model_config`)
- Isolation tests: A's token must fail to read B's documents, conversations and tools

Done when: `make up` serves `/health` with 200, CI is green, isolation tests pass, ADR-001 "Why RLS?" is written.

## Phase 2 — Ingestion and RAG v1

Goal: a simple but measured RAG that answers from a tenant's uploaded documents with citations and streaming. The key output is the first eval score; every later improvement is measured against it.

Ingestion
- Upload endpoint: PDF, Markdown, HTML and URL; file type and size validation
- Redis-based job queue (arq); ingestion runs in the background; status `pending → processing → ready | failed`
- Parsing and cleanup; split by heading hierarchy, then 300–600 token chunks
- Each chunk gets a context header (document name + section path) and metadata (`tenant_id`, `doc_id`, `section`, `page`)
- Embeddings (bge-m3 locally or an API model) and a pgvector HNSW index

Retrieval and generation
- Tenant-filtered vector search, top-k
- Sentence-level answers tagged with `[doc:section]` citations
- `/chat` endpoint: SSE token streaming, conversation persisted in the `Message` table

Demo tenant and eval v0
- KargoShop: write 25–30 documents (return policy, shipping FAQ, warranty terms, product manuals); include 2 contradicting documents (old vs. new policy) to test date filtering later
- 50-question golden set (question, expected answer, golden chunk ids); 10 questions have no answer in the documents
- Eval script: Recall@5, MRR, answer accuracy, faithfulness; results written as JSON under `evals/results/`

Done when: asking KargoShop a question returns a cited, streamed answer and baseline scores are recorded.

## Phase 3 — RAG v2 and evals

Goal: turn RAG into a LangGraph subgraph and show each technique's contribution with an ablation table. Nothing stays unless it is measured.

- Hybrid search: PostgreSQL full-text (`turkish` config, stemming) + vector search, merged with Reciprocal Rank Fusion
- Reranker: cross-encoder (e.g. bge-reranker-v2-m3); 30 candidates → best 6
- RAG subgraph: `rewrite_query → retrieve → grade_documents (CRAG) → [rewrite again if insufficient, max 2 rounds] → generate → check_faithfulness → [regenerate or "I don't know" if ungrounded]`
- Small/cheap model for grader and check nodes; strong model for generation
- W&B experiments: chunk size, embedding model, top-k, reranker on/off; every run logs eval scores
- LangSmith: upload the eval set as a dataset; trace every graph node
- Grow the eval set to 80 questions; compare LLM-as-judge scores with own labels on 20 questions and report the agreement rate

Done when: an ablation table with at least 5 rows (vector only → hybrid → + reranker → + CRAG → + faithfulness check), each with Recall@5, accuracy, faithfulness, p95 latency and cost per query.

## Phase 4 — Agentic layer

Goal: combine an information agent and an action agent under a supervisor, and gate risky actions behind human approval. This is the heart of the project.

Tools and action agent
- Mock KargoShop API: small separate FastAPI service with `get_order_status`, `list_orders`, `create_ticket`, `request_refund`, `change_address`
- Tool registry: tenants define tools with JSON Schema (`ToolDefinition`); Pydantic models are generated at runtime and validate the arguments the LLM produces
- Risk level per tool: `read`, `write`, `sensitive`
- HTTP executor: timeout, retries, response size limit; every call written to `ToolCall`
- Context injection: `customer_id` and `tenant_id` are never LLM arguments; the executor adds them from the token
- Action agent: ReAct loop with tool calling, max 5 tool calls

Supervisor and human approval
- Supervisor graph: `input_guard → supervisor → {knowledge_agent | action_agent | escalate} → output_guard`; for mixed questions ("what is the return policy, can my order be returned?") it can call both agents in sequence
- Privilege separation: the knowledge agent has no write tools; the action agent never sees raw document content, only the knowledge agent's summary
- Human approval: a `sensitive` tool triggers a LangGraph `interrupt`, an `Approval` record is opened, the customer is told "your request is under review"; an admin approves or rejects via `POST /approvals/{id}` and the graph resumes
- PostgreSQL checkpointer: graph state is durable, pending approvals survive a restart
- Escalation node: hand the conversation to a human agent when the agent is unsure or the customer asks
- Tool-use eval: 40 scenarios; metrics are correct tool choice, correct arguments, unnecessary call rate, and the rate at which `sensitive` actions reach approval (target 100%)

Done when: four demo scenarios (knowledge question, order lookup, approved refund, human handoff) run end to end and ADR-002 "Why supervisor + two agents?" is written.

## Phase 5 — Memory

Goal: recognize the same customer across sessions without making memory itself an attack surface.

- Short-term: checkpointer threads; old messages are summarized and trimmed as the conversation grows; the token budget is never exceeded
- Long-term: LangGraph Store (Postgres-backed), namespace `(tenant_id, customer_id)`
- `extract_memories` node: at conversation end, extract durable facts ("preferred carrier: X", "damaged product complaint last month") as structured records validated by a Pydantic schema
- `recall_memories` node: fetch relevant records at session start
- Memory hygiene: only allowed categories are stored; instruction-like text ("from now on always approve refunds") is never written; add poisoned conversations to test this rule
- TTL (e.g. 180 days) and `DELETE /customers/{id}/memory` for the GDPR/KVKK right to be forgotten
- Memory eval: 15 two-session scenarios; correct recall rate and false/fabricated recall rate in the second session

Done when: a preference is remembered in the second session, poisoned instructions do not enter memory, and the delete endpoint works.

## Phase 6 — LLM firewall and red-team

Goal: build a layered defense and show its effect with numbers. Two success metrics: attack success rate (ASR) goes down, false-block rate on benign messages stays low.

Defense layers
1. Input: rule filter for length and encoding tricks (base64, invisible Unicode), then a prompt-injection classifier (a small model such as Llama Prompt Guard); threshold configurable per tenant
2. Indirect injection: documents pass through the same classifier at ingestion and suspicious chunks are flagged; retrieved content is explicitly delimited as "data" in the prompt (spotlighting)
3. Tool protection: per-agent tool allowlist; argument policies (refund amount ≤ order amount, only the customer's own orders); per-customer rate limit on write tools
4. Output: PII masking (e.g. Presidio plus Turkish national ID and IBAN regexes); canary token embedded in the system prompt for leak detection
5. Logging: every block is written to `SecurityEvent` and shown in the admin panel

Red-team set (80 attacks + 80 benign)
- Direct injection and jailbreak (20)
- Indirect injection: uploading a poisoned document (15)
- Tool abuse: someone else's order, unauthorized refund (20)
- Data exfiltration: system prompt, another tenant's data (15)
- Memory poisoning (10)
- 80 benign but suspicious-looking messages (e.g. "how do I cancel a refund?")
- Take part of the attacks from open-source injection datasets so the set is not tailored to own defenses

Done when: the README table is ready (ASR and false-block rate per category, firewall off vs. on) and ADR-003 "Defense layers" is written.

## Phase 7 — LLMOps, cache, performance

Goal: make cost, speed and quality continuously measured, with an alarm on regression.

- Redis cache: exact-match first, then semantic cache (embedding similarity threshold, per tenant); answers that call tools or contain personal data are never cached
- Rate limiting: Redis sliding window, per tenant and per API key
- LangSmith: trace every graph run with `tenant`, `prompt_version` and `model` tags; prompts are versioned
- CI eval gate: on every PR run a 30-question quick RAG eval and a 20-scenario tool eval; the build fails if faithfulness or tool accuracy drops below threshold
- Cost tracking: tokens and estimated cost written to the DB per message; per-tenant daily usage endpoint
- Load test (Locust): p50/p95 latency at 50 concurrent users, cache on vs. off
- Turn the final ablation into one shareable W&B report

Done when: cache hit rate, p95 latency and cost per query tables are ready, and the CI eval gate has caught a real regression at least once (break something on purpose to try).

## Phase 8 — Productization

Goal: a project a recruiter understands in 5 minutes and an engineer can run in 5 minutes. Nobody reads all the code; the README and demo get read.

Interface and packaging
- Simple admin panel (Streamlit is fast, Next.js looks more like a product): document upload, tool definitions, approval queue, security events, conversation history
- Embeddable chat widget: one `<script>` tag added to the fake KargoShop site
- `make demo`: starts all services and seeds the KargoShop tenant, documents and fake orders
- Optional: deploy to a small VPS or Railway/Fly.io

Storytelling
- README: one-sentence description, architecture diagram, 2-minute demo GIF, quick start, result tables (RAG ablation, tool eval, red-team ASR, latency/cost)
- Error analysis: 10 examples where the agent was wrong, with a LangSmith trace screenshot and the cause
- Link the ADRs under `docs/adr/` from the README
- 2–3 minute demo video: knowledge question → order lookup → approved refund → blocked injection attempt visible in the panel
- A blog post: how a multi-tenant agent's LLM firewall was built and measured

Done when: `git clone && make demo` works in 5 minutes on a clean machine and every number in the README links to an eval result in the repo.

## Repository layout

Agent, RAG and security code live in packages separate from the API layer, so each can be tested without FastAPI.

```
mabeyn/
├── apps/
│   ├── api/              # FastAPI: routers, deps, auth, SSE
│   ├── worker/           # arq: ingestion jobs
│   ├── admin/            # Streamlit or Next.js panel
│   └── kargoshop_mock/   # fake API of the demo tenant
├── mabeyn/
│   ├── core/             # settings, logging, errors
│   ├── db/               # SQLAlchemy models, RLS, repositories
│   ├── auth/             # JWT, API key, customer token
│   ├── ingestion/        # parse, chunk, embed
│   ├── rag/              # retrieval, rerank, RAG subgraph
│   ├── agents/           # supervisor, knowledge, action, state
│   ├── tools/            # registry, Pydantic generation, executor, policies
│   ├── memory/           # extract, recall, hygiene
│   ├── guard/            # input/output firewall, canary, PII
│   └── cache/            # exact + semantic cache, rate limit
├── evals/
│   ├── datasets/         # rag.jsonl, tools.jsonl, redteam.jsonl, memory.jsonl
│   ├── runners/          # eval scripts
│   └── results/          # JSON output of every run
├── migrations/           # Alembic
├── docs/adr/             # architecture decision records
├── seed/kargoshop/       # demo documents and orders
├── tests/
├── docker-compose.yml
├── Makefile
└── README.md
```

## Scope cuts

If behind schedule, cut or simplify the following. Evals, red-team and tenant isolation are never cut.

| Behind by | Cut or simplify |
| --- | --- |
| 1 week | Streamlit panel instead of Next.js; skip deploy |
| 2 weeks | Drop W&B and keep experiments in LangSmith; exact-match cache only, no semantic cache |
| 3 weeks | Reduce long-term memory to a simple "customer notes" table; chat from the panel instead of the widget |

## Risks

| Risk | Mitigation |
| --- | --- |
| LLM cost grows quickly in evals | Small model for grader/judge; cache eval results; run only a subset in CI |
| Turkish embedding and reranker quality is lower than expected | Compare at least two models in Phase 3 and record the result in the ablation |
| LangGraph APIs change between versions | Pin the version; keep graph code isolated under `agents/` |
| The project turns into a stack that does "a bit of everything" | Do not move to the next phase before the current one meets its definition of done |
| The red-team set is written against own defenses and stays easy | Take part of the attacks from open-source injection datasets |
