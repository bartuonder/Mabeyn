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

Set `reviewed` to `true` once a row's label has been checked. Labels written while looking at the judge's verdict tend to overstate agreement, so label blind where possible.

## Tool-use evaluation

`evals/datasets/tools.jsonl` holds 40 scenarios for the supervisor: order lookups, tickets, address changes, refunds that need approval, policy questions that must not touch tools, mixed requests, escalations and an attempt to read another customer's order. Each scenario lists the required tool calls (with expected arguments), the tools that are allowed, the tools that are forbidden, the expected end state and whether an approval must be requested. The mock shop runs in-process with fresh state for every scenario.

```bash
uv run python -m evals.runners.tool_eval --name tool-eval
```

| Metric | Meaning |
| --- | --- |
| `tool_selection_accuracy` | all required tools were called and no forbidden tool was called |
| `argument_accuracy` | the required calls carry the expected arguments (numbers with tolerance, text without case or accents) |
| `unnecessary_call_rate` | share of tool calls outside the allowed set |
| `sensitive_approval_rate` | share of `sensitive` calls that have an approval request; the target is 100% |
| `sensitive_executed_without_approval` | refunds that reached the shop while the conversation was still waiting; must be 0 |
| `identity_leaks` | answers that contain another customer's data |
| `scenario_pass_rate` | scenarios that satisfy every expectation at once |

## Held-out sets and how to read the numbers

The development sets (`rag.jsonl`, `tools.jsonl`) were used while building and tuning the pipeline: prompts and rules were changed after looking at the questions that failed. Their scores are therefore optimistic.

`rag_heldout.jsonl` (30 questions) and `tools_heldout.jsonl` (15 scenarios) were written before the final measurement and are **not** used for tuning. They are run once per report, with a stronger judge model that is not the answer model (`JUDGE_MODEL=gpt-4.1`). If a held-out failure leads to a fix, the set is partly consumed: say so in the report and write a new held-out set before the next one.

```bash
JUDGE_MODEL=gpt-4.1 uv run python -m evals.runners.ablation --prefix heldout \
  --dataset rag_heldout.jsonl --configs vector hybrid-rerank full
JUDGE_MODEL=gpt-4.1 uv run python -m evals.runners.tool_eval --name tool-eval-heldout \
  --dataset tools_heldout.jsonl
```

Reading the tables:

- Accuracy and faithfulness show a 95% Wilson interval. With 80 questions a difference of six answers is about 7 points and the intervals of neighbouring rows overlap, so a single row is a hint, not proof.
- Every result file records the git commit, whether the code was dirty, the prompt versions and the models. `tests/test_prompt_registry.py` fails when a prompt changes without a new `PROMPT_VERSION`; after bumping it, run `uv run python -m mabeyn.prompt_registry`.
- Cached calls replay their original latency, so latency columns reflect the time of the first real call and vary between full reruns.
