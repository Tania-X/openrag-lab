# OpenRAG 探索总结：成果与踩坑记录

> 记录我们从 Dify RAG 实践到 OpenRAG 探索的过程、当前成果，以及实际踩过的坑。

---

## 一、已有成果

### 1. Dify RAG 阶段

- 完成了一套可运行的金融知识库 RAG：
  - Dify + Weaviate + Spring Boot + React
- 知识库资产：
  - 50 份金融文档
  - 100+ 条评测题
- 建立了 RAG 评测体系：
  - hit@1 / hit@k / MRR
  - Query Rewrite A/B 对比
  - 元数据过滤对比
- 核心结论：
  - Query Rewrite 对清晰书面问题收益有限，更适合口语化/模糊问题
  - 硬性业务条件（年份、版本、文档类型）应使用元数据过滤，而不是依赖 Rerank

### 2. OpenRAG 探索阶段

- 完成 OpenRAG v0.7.0 源码构建：
  - openrag-backend
  - openrag-frontend
  - openrag-langflow
  - openrag-opensearch
- 启动并跑通：
  - OpenRAG Frontend :3000
  - Langflow :7860
  - OpenSearch :9200
  - OpenSearch Dashboards :5601
  - Docling :5001
- 接入 DeepSeek + SiliconFlow：
  - 通过本地 new-api 网关统一成 OpenAI 兼容 Provider
  - DeepSeek `deepseek-chat` 已验证可对话
  - SiliconFlow `BAAI/bge-m3` 已验证可生成 Embedding
- 已创建 Python 工程仓库 `openrag-lab`：
  - 后续用于 OpenRAG 接入、评测、迁移工具

---

## 二、遇到的坑

### 1. OpenRAG `latest` 镜像与源码 flow 版本不匹配

- 现象：
  - Chat 报 `ModuleNotFoundError: No module named 'lfx.components.models_and_agents.agent_helpers'`
- 原因：
  - `latest` Langflow 镜像里的 `lfx` 版本较旧
  - OpenRAG 最新 flow 依赖新版 `lfx`
- 解决：
  - 固定到 `v0.7.0`
  - 从源码重新构建匹配镜像

### 2. Docker Hub 直连超时

- 现象：
  - 拉镜像 / 构建时 `i/o timeout`
- 解决：
  - 配置 Colima 代理
  - 提前拉取基础镜像
  - 构建时传 `HTTP_PROXY / HTTPS_PROXY` build args

### 3. one-api 不支持 ARM Mac

- 现象：
  - `justsong/one-api` 没有 `linux/arm64` 镜像
- 解决：
  - 改用同源分支 `calciumion/new-api`
  - 功能和使用方式基本一致

### 4. OpenRAG 自定义 Provider 支持不完整

- 现象：
  - 配置 `deepseek` / `siliconflow` 自定义 provider 后
  - Langflow 报缺少 `DEEPSEEK_API_KEY` / `SILICONFLOW_API_KEY`
  - 继续修复后报 `No embedding class defined for BAAI/bge-m3 (provider: siliconflow)`
- 原因：
  - OpenRAG v0.7.0 的 Langflow flow 对自定义 provider 没有完整的 class mapping
  - 不是塞 Key 就能解决的
- 解决：
  - 采用本地 `new-api` 网关
  - 把 DeepSeek / SiliconFlow 统一成 OpenAI 兼容 Provider
  - OpenRAG 使用官方支持的 OpenAI 路径

### 5. 直接改 Langflow SQLite 导致数据库损坏

- 现象：
  - `sqlite3.DatabaseError: database disk image is malformed`
- 原因：
  - 在 Langflow 运行中直接修改 `langflow-data/langflow.db`
- 教训：
  - 不要直接改运行中服务的 SQLite
  - 优先使用官方 API / UI / 后端同步机制

### 6. Langflow 管理员认证失败

- 现象：
  - OpenRAG 后端无法登录 Langflow
  - 报 `500 Internal Server Error` on `/api/v1/login`
- 解决：
  - 设置 `LANGFLOW_ENABLE_SUPERUSER_CLI=true`
  - 重新初始化 Langflow 管理员账号
  - 后端可正常同步 flow 和全局变量

### 7. new-api Embedding 404

- 现象：
  - 网关转发 Embedding 请求到 SiliconFlow 返回 404
- 原因：
  - SiliconFlow 渠道 `base_url` 配成了 `https://api.siliconflow.cn/v1`
  - new-api 会再拼接 `/v1`，导致变成 `/v1/v1/embeddings`
- 解决：
  - `base_url` 改为 `https://api.siliconflow.cn`

### 8. new-api 模型价格未配置

- 现象：
  - Embedding 报 `模型 BAAI/bge-m3 的价格未配置`
- 解决：
  - 开启自用模式：
    - `PUT /api/option/`
    - `{"key":"SelfUseModeEnabled","value":true}`

---

## 三、当前架构

```text
OpenRAG
  ├── Frontend :3000
  ├── Backend
  ├── Langflow :7860
  ├── OpenSearch :9200
  └── Docling :5001
        │
        │  OpenAI 兼容
        ▼
new-api 网关 :3001
  ├── DeepSeek
  │     deepseek-chat
  └── SiliconFlow
        BAAI/bge-m3
        BAAI/bge-reranker-v2-m3
```

---

## 四、后续计划

- 将 Dify 的 50 份文档迁移到 OpenRAG
- 将 100+ 条评测集在 OpenRAG 上跑一轮
- 对比 Dify vs OpenRAG 的 hit@1 / MRR
- 把 Dify 的元数据过滤设计适配到 OpenRAG
- 沉淀 Python 工具链到 `openrag-lab`

---

## 五、2026-09-01 追加：OpenRAG Lab 真实 API 对齐

### 已完成

- `openrag-lab` 的 `client.py` 已从占位接口改为真实 OpenRAG v1 API。
- 已同步 Dify 的 sample-data 与全部评测 CSV 到 `openrag-lab`。
- 已支持：
  - `list-files`
  - 顺序批量 ingest 并等待任务完成
  - 基于 OpenRAG search 的 `hit@1 / hit@k / MRR` 评测
  - `year/version -> data_sources`、`doc_type -> document_types` 的元数据映射
- 已创建 OpenRAG API Key 并写入本地 `.env`（不入库）。

### 新踩坑：Langflow SQLite 再次损坏

- 现象：重启 OpenRAG 后 ingest 报 `No Langflow API key available`，
  手动创建 Langflow API key 报 `database disk image is malformed`。
- 原因：`langflow-data/langflow.db` 损坏（历史直接改库留下的隐患再次暴露）。
- 解决：
  ```bash
  docker compose stop langflow openrag-backend
  mv langflow-data langflow-data.bak-<timestamp>
  mkdir langflow-data
  docker compose up -d langflow
  # 等待 Langflow health OK
  docker compose up -d openrag-backend
  ```
- 教训：不要直接改 Langflow SQLite；如果遇到损坏，优先重置 `langflow-data`。

### 新踩坑：OpenRAG 后端 search embedding 没有走 new-api 网关

- 现象：
  - 第一次检索很慢，日志显示一直请求 `https://api.openai.com/v1/models`
  - 最终 `Failed to embed with model BAAI/bge-m3`，只能退回 keyword 检索
- 原因：
  - OpenRAG 后端 `ModelsService.get_openai_models()` 硬编码 OpenAI 官方地址
  - `patched_embedding_client` 创建时没有读取 `OPENAI_API_BASE / OPENAI_BASE_URL`
  - `agentd` 的 embedding patch 对 `openai/...` 前缀模型会原样发给网关，
    但 new-api 只认识真实的模型名 `BAAI/bge-m3`
- 解决（本地源码补丁）：
  1. `models_service.py`
     - `get_openai_models()` 支持 `OPENAI_API_BASE`，不再超时访问官方
     - `get_litellm_model_name()` 在网关模式下返回 `openai/{model_name}`
  2. `settings.py`
     - 创建 `AsyncOpenAI` 时传入 `base_url`
     - 对 `embeddings.create` 做一层包装，发送前去掉 `openai/` 前缀
  3. 将补丁同步到运行容器并重启 backend
- 效果：第二次检索约 0.5s，向量检索正常返回。

---

## 六、2026-09-01 OpenRAG 首批评测结果（58 份文档已入库）

| 评测集 | 条数 | hit@1 | hit@5 | MRR |
|---|---|---|---|---|
| 通用研发运维（`评测集-questions.csv`） | 14 | 10/14 (71.4%) | 13/14 (92.9%) | 0.8095 |
| 金融知识库（`fintech-评测集-questions.csv`） | 15 | 11/15 (73.3%) | 15/15 (100%) | 0.8278 |
| 金融 Batch1 standard | 25 | 24/25 (96.0%) | 25/25 (100%) | 0.9800 |
| 金融 Batch2 standard | 25 | 24/25 (96.0%) | 25/25 (100%) | 0.9800 |
| 金融 Batch3 standard | 25 | 23/25 (92.0%) | 25/25 (100%) | 0.9533 |
| 异构格式（PDF/DOCX/XLSX/HTML/CSV） | 10 | 8/10 (80.0%) | 10/10 (100%) | 0.8833 |
| 年份元数据（rewrite-ab，无过滤） | 10 | 8/10 (80.0%) | 10/10 (100%) | 0.8667 |
| 年份元数据（rewrite-ab，`year` data_sources 过滤） | 10 | 10/10 (100%) | 10/10 (100%) | 1.0000 |

结论：

- OpenRAG 在 58 份文档上已可跑通完整 ingest + search + eval。
- 与 Dify 阶段结论一致：**硬性年份条件应该用 data_sources/元数据过滤**，
  过滤后年份评测从 `hit@1=8/10` 提升到 `10/10`。
- 当前 batch 评测 hit@5 全部 100%，说明召回充足；后续可以继续看 hit@1 和 badcase。

---

## 七、Dify vs OpenRAG 检索对比（首批）

说明：

- 评测集只使用 Dify/OpenRAG 两边都覆盖的金融文档。
- Dify 知识库 50 份，OpenRAG 58 份（多出的 8 份通用文档不参与金融对比）。
- `baseline` = hybrid_search、无 rerank、无 metadata、无 rewrite。
- `Dify rerank` = Dify 开启 `BAAI/bge-reranker-v2-m3`，OpenRAG 仍为原生 search（当前未暴露 rerank）。
- `metadata` = Dify 用 `metadata_filtering_conditions`，OpenRAG 用 `data_sources` 文件过滤。

### 7.1 金融 15 题

| 配置 | Dify hit@1 | Dify MRR | OpenRAG hit@1 | OpenRAG MRR |
|---|---:|---:|---:|---:|
| baseline | 14/15 (93.3%) | 0.9667 | 11/15 (73.3%) | 0.8278 |
| Dify rerank | 15/15 (100%) | 1.0000 | 11/15 (73.3%) | 0.8278 |

### 7.2 Batch 1/2/3（各 25 题）

| 评测集 | 配置 | Dify hit@1 | Dify MRR | OpenRAG hit@1 | OpenRAG MRR |
|---|---|---|---|---|---|
| Batch1 | baseline | 23/25 (92.0%) | 0.9400 | 24/25 (96.0%) | 0.9800 |
| Batch1 | Dify rerank | 24/25 (96.0%) | 0.9700 | 24/25 (96.0%) | 0.9800 |
| Batch2 | baseline | 19/25 (76.0%) | 0.8600 | 24/25 (96.0%) | 0.9800 |
| Batch2 | Dify rerank | 24/25 (96.0%) | 0.9800 | 24/25 (96.0%) | 0.9800 |
| Batch3 | baseline | 22/25 (88.0%) | 0.9213 | 23/25 (92.0%) | 0.9533 |
| Batch3 | Dify rerank | 24/25 (96.0%) | 0.9800 | 23/25 (92.0%) | 0.9533 |

### 7.3 异构格式（10 题）

| 配置 | Dify hit@1 | Dify MRR | OpenRAG hit@1 | OpenRAG MRR |
|---|---:|---:|---:|---:|
| baseline | 9/10 (90.0%) | 0.9500 | 8/10 (80.0%) | 0.8833 |
| Dify rerank | 9/10 (90.0%) | 0.9500 | 8/10 (80.0%) | 0.8833 |

### 7.4 年份元数据（10 题）

| 配置 | Dify hit@1 | Dify MRR | OpenRAG hit@1 | OpenRAG MRR |
|---|---:|---:|---:|---:|
| 无过滤 | 7/10 (70.0%) | 0.8250 | 8/10 (80.0%) | 0.8667 |
| 有元数据过滤 | 9/10 (90.0%) | 0.9500 | 10/10 (100%) | 1.0000 |

### 7.5 初步解读

- 在简单金融题上，Dify 的 Rerank 优势明显，尤其 `fintech-15` 从 93.3% 提到 100%。
- 在 Batch 2 上，OpenRAG 原生检索反而比 Dify 无 rerank 高 20 个点（96% vs 76%）。
- 加入 Dify Rerank 后，两边 Batch1/Batch2 基本打平，Batch3 Dify 略高。
- 年份元数据过滤两边都有效；OpenRAG 的 `data_sources` 过滤在当前 10 题上做到 100% hit@1。
- 说明：当前对比只是“检索层”，还没有比较生成质量、引用质量、运维成本和可扩展性。
