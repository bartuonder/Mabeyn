| Configuration | Recall@5 | MRR | Accuracy | Faithfulness | p95 latency (s) | Cost / query ($) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Vector only | 0.953 | 0.850 | 0.900 | 0.975 | 1.20 | 0.00081 |
| + Hybrid (FTS + RRF) | 0.969 | 0.883 | 0.912 | 0.963 | 1.20 | 0.00082 |
| + Reranker | 1.000 | 0.984 | 0.950 | 0.988 | 2.84 | 0.00200 |
| + CRAG grading | 1.000 | 0.984 | 0.975 | 0.975 | 7.52 | 0.00195 |
| + Faithfulness check | 1.000 | 0.984 | 0.975 | 0.975 | 7.08 | 0.00203 |
