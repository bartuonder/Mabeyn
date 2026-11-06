# ADR-0002: Why a supervisor and two agents?

- Status: accepted
- Date: 2026-11-01

## Context

The assistant must answer from company documents and also act on a customer's orders. Documents are untrusted input: a poisoned page could contain instructions. Actions change real data and some of them (refunds) must not happen without a human. Conversations can also wait for hours for that human.

## Decision

A supervisor graph routes each customer message to one or two specialist agents.

```
input_guard → supervisor → dispatch ⇄ {knowledge_agent | action agent | escalate} → compose → output_guard
```

- **Knowledge agent**: the RAG subgraph. It reads documents and has no tools at all.
- **Action agent**: a ReAct loop over the tenant's tools, limited to 5 tool calls per request. It never sees raw document content; for mixed questions it receives only the knowledge agent's answer text as policy notes. Retrieved chunks are never stored in the shared graph state, so the separation is structural rather than a prompt rule.
- **Mixed requests** ("what is the return policy and can I return order KS-1001?") run knowledge first, then action. The supervisor writes a clean policy question for the document search without order numbers.
- **Identity** (`tenant_id`, `customer_id`) comes from the verified session and is added by the executor. Tool schemas that expose those names are rejected at registration, and a model-supplied value is a validation error.
- **Risk levels** `read`, `write`, `sensitive` are stored per tool. A `sensitive` call never runs directly: the action agent creates a pending `ToolCall` and an `Approval`, then pauses with a LangGraph `interrupt`.
- **Durability**: graph state lives in a PostgreSQL checkpointer, keyed by `tenant:conversation`. An admin decision (`POST /approvals/{id}`) resumes the thread, even after a restart. The customer receives the result as a new message in the conversation.
- **Escalation** is a node of its own. The supervisor can choose it, and the action agent forces it after three consecutive tool failures. The conversation is marked `escalated` and later customer messages are stored for a human instead of being answered.
- The input and output guards are hooks. Today they only normalise text; the firewall phase fills them in.

## Alternatives considered

- **One agent with documents and all tools**: simpler, but injected document text could trigger tool calls, and tool-use quality could not be measured separately from answer quality.
- **Prebuilt ReAct agent**: no place to pause cleanly for approval or to keep documents away from the tool loop.

## Consequences

- Mixed requests cost more model calls and latency than a single agent.
- Routing mistakes are possible; routing accuracy is part of the tool-use evaluation.
- The checkpoint tables have no tenant column. Their thread id is `<tenant>:<conversation>`, and a policy compares its tenant part with `app.tenant_id` (see [ADR-0006](0006-customer-memory.md) for how the setting reaches pooled connections). Resuming also requires an `Approval` row that is protected on its own.
- Resuming happens inside the approval request. A failure there leaves the approval pending so it can be retried.
- psycopg's async mode needs the selector event loop on Windows; Linux containers are unaffected.
