# ADR-0007: Measuring cost and speed, caching answers, and gating changes on an evaluation

- Status: accepted
- Date: 2026-11-04

## Context

Every message costs model calls and takes seconds. Without numbers the cost cannot be steered, an answer cache can silently serve the wrong answer, and a prompt change can lower quality without anyone noticing. Abuse control so far covered only session creation.

## Decision

- **Cost is written where it is spent.** Each assistant message stores input and output tokens, the number of model calls, the estimated cost, the latency and whether it came from the cache. `GET /usage` sums this per day for the tenant. It covers the answer path (firewall rating, routing, retrieval, generation, checks, cache lookups); summaries of long conversations and memory extraction are not included.
- **Rate limits are sliding windows in Redis.** Chat is limited per customer, per API key and per tenant (the key id travels in the customer token); session creation per address and per key. A limited request gets `429` and `Retry-After`.
- **Only plain knowledge answers are cached.** An answer is stored when it was the first question of a conversation, only the knowledge agent answered, it did not abstain, the output layer did not change it, and neither question nor answer holds personal data or an order number. Entries are per tenant, expire after a day, die with every prompt version change, and are dropped when a document is added, changed or deleted. The lookup runs after the firewall's input layer and the reply passes the output layer, so the cache cannot be used to skip a check.
- **Two lookup layers.** An exact match in Redis on the normalised question, then a semantic candidate in pgvector (similarity above 0.6) that a small model must confirm asks for the same information. Measured on 24 paraphrase pairs and 24 look-alike pairs, similarity alone cannot separate them: paraphrases have a median of 0.69, look-alikes such as "how to redeem" and "how to buy" a gift card reach 0.97, and a threshold of 0.93 would have served a wrong answer for 12.5% of the look-alikes while catching no paraphrase. With the confirmation step the wrong hits were 0 of 24 and 15 of 24 paraphrases were found.
- **A quick evaluation gates pull requests.** 30 RAG questions and 20 tool scenarios run on every pull request from the same repository; the build fails when accuracy, faithfulness, recall, scenario success or tool selection fall below a threshold, or when a sensitive action runs without approval or another customer's data leaks. Thresholds sit about one failure below the measured baseline, because model answers vary a little between runs. The responses of the gate's 50 items are committed as a cache snapshot, so an unchanged prompt costs nothing and only changed prompts call the model; the repository's OpenAI secret pays for those calls and the run is capped at 0.75 USD.
- **Runs carry tags.** Every traced run is tagged with the tenant, every prompt version and the model.

## Alternatives considered

- **Semantic cache on similarity alone.** Cheaper per lookup, but the measurement above shows no safe threshold.
- **Redis Stack vector search.** Would keep everything in Redis; the plain Redis image has no vector index, and pgvector already gives per-tenant isolation through row-level security.
- **A gate that only replays recorded answers.** Free, but it cannot notice that a changed prompt makes the model worse, which is the point of the gate.
- **The full evaluation on every pull request.** More signal, but cost and time grow with the set; the quick sets catch gross regressions and the full sets stay a release-time activity.
- **A shareable Weights & Biases report.** Skipped as allowed by the plan's scope cuts; the ablation tables are in the repository.

## Consequences

- A semantic hit costs one embedding and one small-model call (a fraction of a cent) instead of a full run, so a hit is much cheaper but not free.
- Documents that change outside the API (direct database edits) do not invalidate the cache until the entry expires.
- The confirmation step can still be wrong for questions that differ in a subtle way; the pair set is small and was written by the same team.
- The gate's tool scenario for an expired refund window is flaky (the model sometimes requests the refund); the thresholds leave room for one such miss.
- The snapshot has to be refreshed when a prompt changes on purpose: run the gate once with the API key and commit the new files.
