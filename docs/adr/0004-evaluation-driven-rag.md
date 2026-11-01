# ADR-0004: Evaluation-driven RAG

- Status: accepted
- Date: 2026-11-01

## Context

Every retrieval or generation technique costs latency and money. A technique that is not measured cannot be justified, and a change that silently regresses answer quality will not be noticed. The golden set must also survive changes to chunking, otherwise chunk size experiments are impossible.

## Decision

- Gold references point to a document and a section heading, not to chunk ids. A chunk hits a gold item when it comes from that document and covers that section, so the same dataset scores any chunk size.
- Metrics: Recall@1/3/5/8 and MRR from retrieval; accuracy and faithfulness from an LLM judge; p50/p95 latency and cost per query from a usage meter around each query.
- The judge runs outside the measured section so it never inflates query latency or cost. Its agreement with manual labels is tracked separately.
- Deterministic model calls and embeddings are cached on disk. A cache hit replays tokens and original latency into the meter without touching the spend ledger, so reruns are cheap and the reported cost and latency stay honest.
- Every real call is appended to a spend ledger and a budget guard stops the run when the limit is reached.
- Techniques are added one at a time (vector, hybrid, reranker, CRAG grading, faithfulness check) and each row of the ablation table is a separate run on the same corpus and questions.
- Retrieval experiments (chunk size, embedding model, top-k, reranker) skip generation and judging.
- Results are written as JSON under `evals/results/` and mirrored to Weights & Biases when a key is configured. The golden set is also available as a LangSmith dataset and graph runs are traced there.

## Consequences

- Merging chunks across small sections means a chunk can cover several gold sections; matching therefore checks every section a chunk lists.
- Results depend on the judge model. Agreement with manual labels is reported so the judge's reliability is visible.
- The corpus is small, so retrieval metrics saturate quickly. Rank-sensitive metrics (Recall@1, MRR) and the answer-level metrics separate configurations better than Recall@5.
- Prompts are versioned (`PROMPT_VERSION`); a prompt change invalidates only the cache entries it touches.
