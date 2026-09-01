# Dify vs OpenRAG 对比报告

- 生成时间：2026-09-01T02:38:47+00:00
- top_k：5
- Dify 知识库：b739b795-f558-48dc-8872-625daac22839
- OpenRAG：http://localhost:3000

> 注意：当前只对比检索层。Dify 知识库为 50 份金融文档，OpenRAG 为 58 份；
> 报告只使用两边都覆盖的金融评测集。

| 实验 | 说明 | 条数 | Dify hit@1 / hit@k / MRR | OpenRAG hit@1 / hit@k / MRR | Dify hit@1 胜出 |
|---|---|---:|---|---|---|
| 金融 15 题 baseline | hybrid_search，无 rerank，无 metadata | 15 | 14/15 (93.3%) / 15/15 (100.0%) / MRR 0.9667 | 11/15 (73.3%) / 15/15 (100.0%) / MRR 0.8278 | ✅ |
| 金融 15 题 Dify rerank | Dify 开启 BAAI/bge-reranker-v2-m3 | 15 | 15/15 (100.0%) / 15/15 (100.0%) / MRR 1.0000 | 11/15 (73.3%) / 15/15 (100.0%) / MRR 0.8278 | ✅ |
| 金融 15 题 OpenRAG rerank | OpenRAG search + BAAI/bge-reranker-v2-m3 | 15 | 14/15 (93.3%) / 15/15 (100.0%) / MRR 0.9667 | 14/15 (93.3%) / 15/15 (100.0%) / MRR 0.9667 | = |
| 金融 15 题 双端 rerank | Dify rerank vs OpenRAG rerank | 15 | 15/15 (100.0%) / 15/15 (100.0%) / MRR 1.0000 | 14/15 (93.3%) / 15/15 (100.0%) / MRR 0.9667 | ✅ |
| Batch1 baseline | hybrid_search，无 rerank，无 metadata | 25 | 23/25 (92.0%) / 25/25 (100.0%) / MRR 0.9400 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | ❌ |
| Batch1 Dify rerank | configs/eval/fintech-batch1-评测集-questions-standard.csv | 25 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9700 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | = |
| Batch1 OpenRAG rerank | configs/eval/fintech-batch1-评测集-questions-standard.csv | 25 | 23/25 (92.0%) / 25/25 (100.0%) / MRR 0.9400 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | ❌ |
| Batch1 双端 rerank | configs/eval/fintech-batch1-评测集-questions-standard.csv | 25 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9700 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | = |
| Batch2 baseline | hybrid_search，无 rerank，无 metadata | 25 | 19/25 (76.0%) / 24/25 (96.0%) / MRR 0.8600 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | ❌ |
| Batch2 Dify rerank | configs/eval/fintech-batch2-评测集-questions-standard.csv | 25 | 23/25 (92.0%) / 24/25 (96.0%) / MRR 0.9400 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | ❌ |
| Batch2 OpenRAG rerank | configs/eval/fintech-batch2-评测集-questions-standard.csv | 25 | 19/25 (76.0%) / 24/25 (96.0%) / MRR 0.8600 | 25/25 (100.0%) / 25/25 (100.0%) / MRR 1.0000 | ❌ |
| Batch2 双端 rerank | configs/eval/fintech-batch2-评测集-questions-standard.csv | 25 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | 25/25 (100.0%) / 25/25 (100.0%) / MRR 1.0000 | ❌ |
| Batch3 baseline | hybrid_search，无 rerank，无 metadata | 25 | 22/25 (88.0%) / 25/25 (100.0%) / MRR 0.9213 | 23/25 (92.0%) / 25/25 (100.0%) / MRR 0.9533 | ❌ |
| Batch3 Dify rerank | configs/eval/fintech-batch3-评测集-questions-standard.csv | 25 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | 23/25 (92.0%) / 25/25 (100.0%) / MRR 0.9533 | ✅ |
| Batch3 OpenRAG rerank | configs/eval/fintech-batch3-评测集-questions-standard.csv | 25 | 22/25 (88.0%) / 25/25 (100.0%) / MRR 0.9213 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9700 | ❌ |
| Batch3 双端 rerank | configs/eval/fintech-batch3-评测集-questions-standard.csv | 25 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9800 | 24/25 (96.0%) / 25/25 (100.0%) / MRR 0.9700 | = |
| 异构格式 baseline | PDF/DOCX/XLSX/HTML/CSV | 10 | 9/10 (90.0%) / 10/10 (100.0%) / MRR 0.9500 | 8/10 (80.0%) / 10/10 (100.0%) / MRR 0.8833 | ✅ |
| 异构格式 Dify rerank | configs/eval/fintech-heterogeneous-评测集-questions.csv | 10 | 9/10 (90.0%) / 10/10 (100.0%) / MRR 0.9500 | 8/10 (80.0%) / 10/10 (100.0%) / MRR 0.8833 | ✅ |
| 异构格式 OpenRAG rerank | configs/eval/fintech-heterogeneous-评测集-questions.csv | 10 | 9/10 (90.0%) / 10/10 (100.0%) / MRR 0.9500 | 9/10 (90.0%) / 10/10 (100.0%) / MRR 0.9333 | = |
| 异构格式 双端 rerank | configs/eval/fintech-heterogeneous-评测集-questions.csv | 10 | 9/10 (90.0%) / 10/10 (100.0%) / MRR 0.9500 | 9/10 (90.0%) / 10/10 (100.0%) / MRR 0.9333 | = |
| 年份题 无过滤 | original_query | 10 | 7/10 (70.0%) / 10/10 (100.0%) / MRR 0.8250 | 8/10 (80.0%) / 10/10 (100.0%) / MRR 0.8667 | ❌ |
| 年份题 元数据过滤 | Dify metadata_filtering_conditions / OpenRAG data_sources | 10 | 9/10 (90.0%) / 10/10 (100.0%) / MRR 0.9500 | 10/10 (100.0%) / 10/10 (100.0%) / MRR 1.0000 | ❌ |
| Query Rewrite original | 使用评测集预生成的 original_query | 15 | 14/15 (93.3%) / 15/15 (100.0%) / MRR 0.9667 | 11/15 (73.3%) / 15/15 (100.0%) / MRR 0.8278 | ✅ |
| Query Rewrite rewritten | 使用评测集预生成的 rewritten_query | 15 | 14/15 (93.3%) / 15/15 (100.0%) / MRR 0.9667 | 10/15 (66.7%) / 14/15 (93.3%) / MRR 0.7889 | ✅ |

## 说明

- `hit@1 / hit@5 / MRR` 均来自检索结果，不涉及 LLM 生成。
- `Dify rerank` 表示 Dify 开启 `BAAI/bge-reranker-v2-m3`；`OpenRAG rerank` 表示 OpenRAG `/api/v1/search` 开启同一 reranker。
- `元数据过滤` 在 Dify 使用 `metadata_filtering_conditions`，在 OpenRAG 使用 `data_sources` 文件名过滤。
- Query Rewrite 使用评测集里预生成的 `rewritten_query`，不是在线调用改写服务。
