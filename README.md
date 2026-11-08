<div align="center">

<img src="docs/media/banner.svg" alt="Mabeyn: a secure, multi-tenant support agent you embed with one script tag" width="100%">

[![CI](https://github.com/bartuonder/Mabeyn/actions/workflows/ci.yml/badge.svg)](https://github.com/bartuonder/Mabeyn/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-DC382D?logo=redis&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?logo=langchain&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Answers from a company's own documents, does real work through its APIs, asks a human before anything risky, remembers returning customers, and sits behind an LLM firewall. The quality claims below are measured, and each measurement links to the file it came from.**

[▶ Watch the demo](docs/media/demo.mp4) · [Try it in five minutes](#-try-it-in-five-minutes) · [Measured results](#-measured-not-claimed) · [Architecture](#-how-it-works) · [Known limits](#-known-limits)

</div>

<p align="center">
  <img src="docs/media/demo.gif" alt="A refund request waits for a human, the admin approves it, and the answer reaches the customer" width="92%">
  <br>
  <sub>A customer asks for a refund, the assistant stops and waits, the shop's admin approves it, and the answer reaches the customer's chat without a reload. The full five-minute walk-through with captions (no sound) is <a href="docs/media/demo.mp4">docs/media/demo.mp4</a>.</sub>
</p>

## ✨ What it does

| 📚 **Grounded answers** | 🛠️ **Real work** | 🧑‍⚖️ **A human decides** |
| --- | --- | --- |
| Hybrid retrieval (vectors and full text), a reranker, relevance grading and a faithfulness check. Every answer cites `[document:section]`. | Tenants register HTTP tools with a JSON Schema and a risk level. The platform, not the model, supplies the customer's identity, and policies check the real record before anything runs. | Sensitive tools such as refunds pause the conversation until an admin decides. The pause survives restarts, expires after 72 hours, and a signed webhook announces new requests. |
| 🧠 **Memory** | 🛡️ **LLM firewall** | 🏢 **Multi-tenant** |
| Facts a customer states are kept for 180 days in a store scoped to that customer. Instructions, claims of status and personal data are never stored; customers and admins can read and erase everything. | Four layers: input (rules and a classifier that reads the recent conversation), documents (scanned on ingestion), tools (allowlist, policies, rate limit) and output (canary token, PII masking). Every block is an auditable event. | PostgreSQL Row-Level Security on every table, including the agent's checkpoint and memory tables, so isolation is enforced by the database and not only by application code. |

Also included: a semantic answer cache, per-customer and per-key rate limits, token and cost tracking, an embeddable chat widget, an admin panel, and an evaluation harness that gates every pull request.

## 📸 Screenshots

<table>
  <tr>
    <td width="50%"><a href="docs/media/screenshots/01-storefront-chat.png"><img src="docs/media/screenshots/01-storefront-chat.png" alt="Chat widget on the demo shop" width="100%"></a><br><sub><b>Chat widget.</b> Cited answers on the shop's own site, in the customer's language.</sub></td>
    <td width="50%"><a href="docs/media/screenshots/03-admin-overview.png"><img src="docs/media/screenshots/03-admin-overview.png" alt="Admin overview" width="100%"></a><br><sub><b>Overview.</b> Pending approvals, blocked requests, cost and documents at a glance.</sub></td>
  </tr>
  <tr>
    <td><a href="docs/media/screenshots/04-admin-approval.png"><img src="docs/media/screenshots/04-admin-approval.png" alt="An approval request" width="100%"></a><br><sub><b>Approvals.</b> Every sensitive action waits here with its exact arguments.</sub></td>
    <td><a href="docs/media/screenshots/05-admin-conversation-memory.png"><img src="docs/media/screenshots/05-admin-conversation-memory.png" alt="A conversation with its security events and the customer's memory" width="100%"></a><br><sub><b>Conversations.</b> The full thread, the security events it triggered and exactly what the assistant remembers.</sub></td>
  </tr>
  <tr>
    <td><a href="docs/media/screenshots/08-admin-security.png"><img src="docs/media/screenshots/08-admin-security.png" alt="Security events and firewall settings" width="100%"></a><br><sub><b>Security.</b> Every block and flag, and the firewall's settings.</sub></td>
    <td><a href="docs/media/screenshots/07-admin-tools.png"><img src="docs/media/screenshots/07-admin-tools.png" alt="Tools with risk levels and policies" width="100%"></a><br><sub><b>Tools.</b> Risk levels and the argument policies that guard each tool.</sub></td>
  </tr>
  <tr>
    <td><a href="docs/media/screenshots/06-admin-documents.png"><img src="docs/media/screenshots/06-admin-documents.png" alt="Knowledge base documents" width="100%"></a><br><sub><b>Knowledge base.</b> Upload PDF, Markdown or HTML and watch it being processed.</sub></td>
    <td><a href="docs/media/screenshots/09-admin-usage.png"><img src="docs/media/screenshots/09-admin-usage.png" alt="Usage and cost" width="100%"></a><br><sub><b>Usage.</b> Tokens, estimated cost and cache hits per day.</sub></td>
  </tr>
  <tr>
    <td><a href="docs/media/screenshots/02-storefront-orders.png"><img src="docs/media/screenshots/02-storefront-orders.png" alt="The shop's order list after an approved refund" width="100%"></a><br><sub><b>The shop's side.</b> After the admin approves, the shop's own order list shows the refund.</sub></td>
    <td><a href="docs/media/screenshots/10-admin-overview-dark.png"><img src="docs/media/screenshots/10-admin-overview-dark.png" alt="Admin overview in dark mode" width="100%"></a><br><sub><b>Dark mode.</b> The panel follows the system setting.</sub></td>
  </tr>
</table>

## 🧭 How it works

```mermaid
flowchart LR
    Customer([Customer]) -->|chat, SSE| Guard[input guard<br/>rules and classifier]
    Guard --> Cache{answer cache}
    Cache -->|miss| Memory[recall memories]
    Memory --> Supervisor{supervisor}
    Supervisor --> Knowledge[knowledge agent<br/>documents only]
    Supervisor --> Action[action agent<br/>tenant tools]
    Supervisor --> Human[hand over to a human]
    Action -->|sensitive tool| Approval[[approval queue]]
    Approval -->|admin decides| Action
    Knowledge --> Reply[output guard<br/>canary, PII masking]
    Action --> Reply
    Human --> Reply
    Cache -->|hit| Reply
    Reply --> Customer
    Admin([Admin panel]) --> Approval
    Docs[documents] --> Worker[worker: parse, chunk, embed] --> PG[(PostgreSQL<br/>pgvector, RLS)]
    Knowledge --> PG
```

The assistant answers only from retrieved documents, only acts through registered tools, and never executes a sensitive action without a recorded human decision.


- Tenant isolation is enforced by PostgreSQL Row-Level Security ([ADR-0001](docs/adr/0001-row-level-security.md)), including the checkpoint and memory tables ([ADR-0008](docs/adr/0008-reaching-the-platform.md)).
- Answers cite `[document:section]`; the citation points at the section inside the retrieved chunk that holds the fact.
- The full workflow, with diagrams generated from the code, is in [docs/architecture.md](docs/architecture.md).
- Retrieval combines pgvector similarity with Turkish/English full text search (Reciprocal Rank Fusion), then an LLM reranker narrows 30 candidates to 6.
- A supervisor routes each message to a knowledge agent, an action agent or a human ([ADR-0002](docs/adr/0002-supervisor-and-two-agents.md)). Tools are registered per tenant with a JSON Schema and a risk level (`read`, `write`, `sensitive`); the platform, not the model, supplies the customer identity.
- `sensitive` tools such as refunds never run directly: the graph pauses, an admin approves or rejects, and the paused conversation survives restarts. Walk-through: [docs/demo-scenarios.md](docs/demo-scenarios.md).
- An LLM firewall guards four places ([ADR-0003](docs/adr/0003-defense-layers.md)): customer messages (rules and a small-model rating), uploaded documents (flagged passages never reach retrieval), tool calls (allowlist, refund checked against the real order, rate limit) and replies (canary token, personal data masking). Every block is a security event the admin can read.
- Returning customers are recognised: long conversations are summarised inside a token budget, and facts a customer states about themselves (a preferred carrier, an earlier complaint) are kept for 180 days in a store scoped to the customer. Instructions, permissions and personal data are never stored, and customers and admins can read and erase everything ([ADR-0006](docs/adr/0006-customer-memory.md)).


## 🧰 Tech stack and concepts

| | |
| --- | --- |
| **Backend** | Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · Pydantic · arq |
| **Data** | PostgreSQL 17 · pgvector · full-text search · Row-Level Security · Redis |
| **AI** | LangGraph (supervisor, ReAct agents, interrupts, Postgres checkpointer) · OpenAI models (`gpt-4.1-mini`, `gpt-4o-mini`, `text-embedding-3-small`) · LangSmith tracing |
| **Frontend** | Next.js 16 · React 19 · TypeScript · server actions · an embeddable, dependency-free widget |
| **Quality** | pytest (700+ tests) · ruff · mypy (strict) · Playwright browser tests · an evaluation harness with a CI gate · Locust load tests |
| **Infrastructure** | Docker Compose · GitHub Actions |

**Concepts it puts into practice:** retrieval-augmented generation (hybrid search, reciprocal rank fusion, reranking, corrective grading, faithfulness checks) · agent orchestration (supervisor routing, tool use, human-in-the-loop interrupts, durable checkpoints) · LLM security (defence in depth against prompt injection, spotlighting, canary tokens, PII masking, tool allowlists and argument policies) · scoped long-term memory with hygiene screens · multi-tenancy with Row-Level Security · evaluation (golden sets, held-out discipline, Wilson intervals, judge calibration, red-teaming, a pull-request gate) · LLMOps (semantic answer cache, sliding-window rate limits, cost tracking, prompt versioning).

## 🔬 Measured, not claimed

| What was measured | Result | Where it comes from |
| --- | --- | --- |
| Answers from the documents, 80 development questions | accuracy 0.95 [0.88-0.98], faithfulness 0.975, the right passage in the top 5 for every question | [`devfull-v7`](evals/results/20261105-232023_devfull-v7.json) |
| The same on 30 fresh held-out questions, judged by a stronger model, measured once | accuracy 0.97 [0.83-0.99], faithfulness 1.00 | [`heldout2-full`](evals/results/20261101-171719_heldout2-full.json) |
| Tool use on 15 fresh held-out scenarios | 13 of 15 passed [0.62-0.96]; no sensitive action without approval, no identity leak | [`tool-eval-heldout2`](evals/results/tool-eval-heldout2.md) |
| Memory across two sessions, 12 fresh scenarios | 11 of 12 passed [0.65-0.99]; no data of another customer, no poisoned note stored | [`memory-eval-heldout-v2`](evals/results/memory-eval-heldout-v2.md) |
| 80 attacks (injection, tool abuse, poisoned documents, exfiltration, memory poisoning) | firewall on: 0 worked [0-0.05]; firewall off: 9 worked; 1 of 80 harmless messages blocked | [`redteam-eval-after`](evals/results/redteam-eval-after.md) |
| The open `deepset/prompt-injections` test set | only 23 of 44 attacks blocked, 1 of 56 harmless blocked: the input layer is not a general jailbreak detector | [`redteam-eval`](evals/results/redteam-eval.md) |
| 50 simulated customers, answer cache off and on | knowledge questions 7.6-10 s -> 1.1 s median, $0.0016-0.0018 -> $0.0005-0.0008 per message, no failed request | [`loadtest-after`](evals/results/loadtest-after.md), [`loadtest-after-repeat`](evals/results/loadtest-after-repeat.md) |
| Pull-request evaluation gate | 20 of 20 scenarios and 29 of 30 questions pass; a deliberately broken prompt fails it | [`gate`](evals/results/gate.md) |

Intervals are 95% Wilson intervals, the sets are small, and the attacks were written by the people who wrote the defences. [docs/results.md](docs/results.md) has every table and says what each number does and does not show; [docs/error-analysis.md](docs/error-analysis.md) traces ten failures to their causes, fixed and not; [docs/evaluation.md](docs/evaluation.md) explains how every number is produced.

## 🚀 Try it in five minutes

You need Docker and an OpenAI API key.

```bash
git clone https://github.com/bartuonder/Mabeyn.git && cd Mabeyn
cp .env.example .env        # add OPENAI_API_KEY
make demo                   # services, migrations, a demo shop, 32 documents, 5 tools
```

Open the storefront at <http://localhost:9100>, pick a demo customer and use the chat bubble; decide approvals in the admin panel at <http://localhost:3100> (the login is printed by `make demo`). API examples and the widget snippet are under [Using it](#-using-it).

## 🔌 Using it

`make demo` (see [above](#-try-it-in-five-minutes)) needs only Docker and an OpenAI API key; [uv](https://docs.astral.sh/uv/) is needed only to run the tests and evaluations. The demo admin is `admin@kargoshop.example` with the password `demo-admin-password` (set `DEMO_ADMIN_PASSWORD` before the first run to change it; an existing tenant keeps its password). The same steps by hand:

```bash
docker compose up -d --build         # api, worker, mock shop, postgres (pgvector), redis; migrations run first
docker compose --profile seed run --rm seed
```

Create a customer session and chat (streaming Server-Sent Events):

```bash
TOKEN=$(curl -s -X POST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email": "admin@kargoshop.example", "password": "demo-admin-password"}' | jq -r .access_token)
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

API keys come in two kinds ([ADR-0005](docs/adr/0005-customer-session-trust.md)). Secret keys (`"kind": "secret"`, the default) stay on the tenant's server and open sessions for a named customer, as above. Publishable keys (`"kind": "publishable"`, optionally with `allowed_origins`) are safe in a browser but only open anonymous sessions: `POST /sessions` with `{}` returns a token for `anon:<random>` that gets no memory and no customer tools.

Admins use `GET` and `DELETE /customers/{customer_id}/memory` for the same, for example to answer a deletion request.

Documents are added with `POST /documents` (PDF, Markdown, HTML upload) or `POST /documents/url`. Tools are managed with `POST /tools` and `DELETE /tools/{id}` (a tool can carry `policies` that check its arguments against a real record, see [ADR-0003](docs/adr/0003-defense-layers.md)); pending refunds are listed with `GET /approvals?status=pending` and decided with `POST /approvals/{id}`. Set `PUT /settings/notifications` with an `approval_webhook_url` to get a signed `approval.pending` event for every new request.

### Admin panel

<http://localhost:3100> (Next.js, `apps/admin`) is where a tenant admin runs the assistant: decide pending approvals (with the arguments, the customer and the conversation next to the decision), upload and delete documents and watch them being processed, register tools with their argument policies, read the security events and tune the firewall and the approval webhook, browse conversations (and erase what the assistant remembers about a customer), see usage and cost per day, clear the answer cache, and manage API keys. Sign in with the demo admin login that `make demo` prints.

The panel talks to the API only from its own server, with the admin's tokens kept in httpOnly cookies (an expired access token is renewed with the refresh token before a page renders), so the browser never sees a token and the API needs no CORS rule for it. It is built in Docker, so the machine needs no Node.

### Chat widget and demo storefront

`make demo` also starts a fictional shop at <http://localhost:9100>. Pick a demo customer in the header and open the chat bubble: a visitor gets an anonymous session, a signed-in customer gets order lookups, refunds that wait for a human, and memory. Approve the refund in the admin panel and the answer appears in the widget by itself.

A tenant embeds the widget with one script tag. For anonymous visitors use a publishable key (it is safe in a page and checks the site's origin):

```html
<script src="https://YOUR-API/widget.js" data-key="mbn_pub_..." async></script>
```

For signed-in customers the shop's own server opens the session with its secret key and the page points the widget at that endpoint (see `apps/kargoshop_site`):

```html
<script src="https://YOUR-API/widget.js" data-session-url="/support/session" async></script>
```

The widget streams answers, shows the sources an answer cites, lets the customer start a new chat (which stores what is worth remembering) and keeps the conversation across page loads. Optional attributes: `data-title`, `data-greeting`, `data-color`, `data-api`. The API allows browser calls from any origin because every call carries a bearer token or key and never a cookie (`CORS_ALLOW_ORIGINS` narrows it).

## 🧪 Development

```bash
uv sync
docker compose up -d postgres redis
make migrate
make test lint typecheck
```

Tests and evaluations send traces to LangSmith when `.env` enables tracing; tests switch it off themselves, and `LANGSMITH_TRACING=false` silences it for a single run (useful once the monthly trace quota of a free account is used up).

## 🚧 Known limits

- The input layer of the firewall is not a general jailbreak detector: it blocked 23 of 44 attacks of an open prompt-injection set. Rules and the classifier catch known phrases and context-dependent triggers, not every paraphrase.
- The small models that grade passages and verify answers are the weak point of answer quality: they sometimes drop the passage that answers a question or reject a correct answer ([error analysis](docs/error-analysis.md)).
- Argument policies check one record per call (a lookup, then equality, amount and date checks).
- Refund requests are the weakest tool-calling case: the agent sometimes promises a refund in words without calling the tool. Nothing is executed then, and a follow-up message gets the call made.
- An approval webhook is the only notification channel, and a human colleague has no endpoint yet to answer an escalated conversation.
- A customer's identity is only as trustworthy as the API key that opened the session ([ADR-0005](docs/adr/0005-customer-session-trust.md)).
- The demo is meant for a local machine: default passwords and secrets are for the demo only, there is no TLS or deployment configuration, and the load tests are one-machine comparisons, not capacity figures.

The full list is in [docs/architecture.md](docs/architecture.md#13-known-limitations).

## 📁 Repository layout

| Path | What it holds |
| --- | --- |
| `mabeyn/` | The core packages: `agents` (supervisor, action agent, approvals), `rag`, `guard` (the firewall), `memory`, `cache`, `tools`, `ingestion`, `db`, `llm` |
| `apps/api`, `apps/worker` | The FastAPI service and the background worker (ingestion, memory extraction, approval expiry and notification) |
| `apps/admin` | The admin panel (Next.js) |
| `apps/kargoshop_mock`, `apps/kargoshop_site` | The demo shop's backend and its storefront with the chat widget |
| `evals/` | Datasets, runners, the pull-request gate and every result file |
| `loadtest/`, `demo/` | The load test and the scripts that record the demo video and the screenshots |
| `migrations/`, `seed/` | Database migrations and the demo tenant's documents, tools and orders |
| `docs/` | [Architecture](docs/architecture.md), [design decisions](docs/adr), [evaluation method](docs/evaluation.md), [results](docs/results.md), [error analysis](docs/error-analysis.md), [demo scenarios](docs/demo-scenarios.md), [roadmap](docs/ROADMAP.md) |

## 📄 License

[MIT](LICENSE) © 2026 Bartu Önder
