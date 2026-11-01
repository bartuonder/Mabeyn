# Evaluation

All quality, latency and cost numbers in this repository come from the runners in `evals/`. Design rationale is in [ADR-0004](adr/0004-evaluation-driven-rag.md).

## Datasets

| File | Questions | Notes |
| --- | --- | --- |
| `evals/datasets/rag.jsonl` | 80 (set `v0`: 50, set `v1`: 30) | Turkish, 16 unanswerable, includes questions where an old and a new policy contradict each other |
| `evals/datasets/rag_en.jsonl` | 20 | English documents for international customers, 3 unanswerable |
| `evals/datasets/judge_calibration.jsonl` | 20 | Judge verdicts next to manual labels |

Each answerable question lists gold references as `(document file, section heading)`. A retrieved chunk hits a gold item when it comes from that document and covers that section.

## Metrics

- **Recall@k** — share of gold references found in the top k chunks (answerable questions only).
- **MRR** — reciprocal rank of the first chunk that hits a gold reference.
- **Accuracy** — LLM judge compares the answer with the reference answer; for unanswerable questions the answer must decline without inventing facts.
- **Faithfulness** — LLM judge checks that every claim is supported by the sources the answer was generated from.
- **Latency / cost** — measured around the pipeline only (judge excluded). Cache hits replay the original latency and token counts.

## Running

```bash
docker compose up -d postgres redis
make migrate

make eval            # one configuration, prints metrics, writes evals/results/*.json
make ablation        # the five-row ablation table
make experiments     # chunk size, embedding model, reranker (retrieval only)
```

Useful flags: `--config`, `--sets v0`, `--limit 10`, `--chunk fine|medium|default|coarse`, `--embedding text-embedding-3-large`, `--retrieval-only`.

Configurations are defined in `evals/configs.py`:

| Name | Retrieval | Reranker | CRAG grading | Faithfulness check |
| --- | --- | --- | --- | --- |
| `vector` | vector, top 5 | no | no | no |
| `hybrid` | vector + full text, top 5 | no | no | no |
| `hybrid-rerank` | hybrid, 30 candidates → 6 | yes | no | no |
| `hybrid-rerank-crag` | hybrid, 30 → 6 | yes | yes | no |
| `full` | hybrid, 30 → 6 | yes | yes | yes |

## Cost control

- Deterministic model calls and embeddings are cached under `evals/.cache/` (not committed).
- Every real call is appended to `evals/.ledger.jsonl`. A run stops when the total reaches `EVAL_BUDGET_USD` (default 4).
- Small models handle rewriting, grading, reranking and faithfulness checks; the judge and the answer generator use the larger model (`LLM_MODEL`, `JUDGE_MODEL`, `LLM_MODEL_SMALL`).

## Experiment tracking

- Results are always written to `evals/results/`.
- Set `WANDB_API_KEY` to also log every run to Weights & Biases (project `mabeyn`).
- Set `LANGSMITH_API_KEY` and `LANGSMITH_TRACING=true` to trace graph runs and model calls, tagged with tenant, prompt version and model. `python -m evals.runners.langsmith_upload` uploads the golden sets as LangSmith datasets.

## Judge calibration

```bash
python -m evals.runners.judge_agreement sample evals/results/<run>.json   # draws 20 answers
# fill in "label" (true/false) and set "reviewed": true in judge_calibration.jsonl
python -m evals.runners.judge_agreement                                    # agreement and Cohen's kappa
```

Calibration labels are provisional until `reviewed` is set to `true` for every row. Labels written while looking at the judge's verdict tend to overstate agreement, so label blind where possible.
