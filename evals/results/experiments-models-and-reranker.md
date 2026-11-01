### embedding

| Configuration | Recall@1 | Recall@3 | Recall@5 | Recall@8 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| text-embedding-3-small, vector | 0.766 | 0.938 | 0.953 | 0.969 | 0.852 |
| text-embedding-3-small, hybrid | 0.812 | 0.969 | 0.969 | 0.969 | 0.883 |
| text-embedding-3-large, vector | 0.812 | 0.969 | 0.969 | 0.984 | 0.882 |
| text-embedding-3-large, hybrid | 0.844 | 0.969 | 0.984 | 0.984 | 0.902 |

### reranker

| Configuration | Recall@1 | Recall@3 | Recall@5 | Recall@8 | MRR |
| --- | ---: | ---: | ---: | ---: | ---: |
| hybrid, reranker off | 0.812 | 0.969 | 0.969 | 0.969 | 0.883 |
| hybrid, reranker on | 0.969 | 1.000 | 1.000 | 1.000 | 0.984 |
