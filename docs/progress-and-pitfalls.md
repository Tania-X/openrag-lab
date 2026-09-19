# OpenRAG 探索总结：成果与踩坑记录

> 当前运行基线：OpenRAG v0.7.1（基于 v0.7.1 + 本地补丁）。


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

- 完成 OpenRAG v0.7.1 源码构建：
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
  - 固定到当时的 `v0.7.0`（后续已升级到 v0.7.1）
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
  - 当时的 OpenRAG v0.7.0 Langflow flow 对自定义 provider 没有完整的 class mapping
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

### 7.6 Query Rewrite A/B（使用评测集预生成的 rewritten_query）

金融 15 题：

| Query | Dify hit@1 | Dify MRR | OpenRAG hit@1 | OpenRAG MRR |
|---|---:|---:|---:|---:|
| original_query | 14/15 (93.3%) | 0.9667 | 11/15 (73.3%) | 0.8278 |
| rewritten_query | 14/15 (93.3%) | 0.9667 | 10/15 (66.7%) | 0.7889 |

初步结论：在这批书面化清晰问题上，Query Rewrite 对 Dify 无增益，对 OpenRAG 甚至略降，
与 Dify 阶段结论一致——Rewrite 不应无条件使用。

---

## 八、OpenRAG 已接入 Rerank

### 实现方式

- 在 OpenRAG 后端 `/api/v1/search` 增加可选参数：
  - `rerank: true`
  - `rerank_model`
  - `rerank_top_n`
- 通过本地 new-api 网关调用 OpenAI 兼容的 `/v1/rerank`
- 使用模型：`BAAI/bge-reranker-v2-m3`
- 如果 rerank 调用失败，自动回退到原始 OpenSearch 结果，不影响检索可用性

### 对比效果（加入 OpenRAG rerank 后）

| 实验 | Dify hit@1 | OpenRAG 无 rerank | OpenRAG rerank |
|---|---:|---:|---:|
| 金融 15 题 | 15/15 (100%) | 11/15 (73.3%) | 14/15 (93.3%) |
| Batch1 | 24/25 (96%) | 24/25 (96%) | 24/25 (96%) |
| Batch2 | 24/25 (96%) | 24/25 (96%) | 25/25 (100%) |
| Batch3 | 24/25 (96%) | 23/25 (92%) | 24/25 (96%) |
| 异构格式 | 9/10 (90%) | 8/10 (80%) | 9/10 (90%) |

结论：OpenRAG 接入 rerank 后，和 Dify 的差距明显缩小，在 Batch2 上甚至反超。

---

## 九、踩坑：OpenRAG API Key 链路会「每 7 天坏一次」

### 现象（2026-09-11）

用 `.env` 里的 `OPENRAG_API_KEY` 调公开 API：

```text
POST /api/v1/search        → {"error": "AuthenticationException(401, 'Unauthorized')"}
GET  /api/v1/files/get_all → {"error": "Authentication failed: OpenSearch rejected the credential..."}
```

但 OpenSearch 集群是 green，admin basic auth 正常，Web UI 也正常。

### 根因

```text
OpenRAG backend 在进程内缓存一个 anonymous JWT（SessionManager._anonymous_jwt），
TTL = 7 天，复用时不检查过期。
API Key 请求的 user.jwt_token 为空 → 走这个缓存 token → OpenSearch 拒绝。
```

时间线对得上：容器 2026-09-03 17:26 启动 → token 于 2026-09-10 17:26 过期 →
2026-09-11 起所有 API Key 请求 401。

### 定位证据

| 证据 | 结果 |
|---|---|
| OpenSearch 审计日志 `/documents/_search` FAILED_LOGIN 条数 | 09-08/09/10 = 0，09-11 = 10 |
| 容器内新建 `SessionManager()` mint 新 token 后直连 OpenSearch | 200 ✅ |
| 把有效 JWT 放进 `/api/v1/search` 的 Authorization 头 | 正常返回结果 ✅ |
| 人为造过期 JWT 直连 OpenSearch | `Unauthorized`，日志同样打 `No 'Authorization' header`（误导性） |

### 处理

```bash
docker restart openrag-backend   # 立刻恢复
```

上游修复方向：复用缓存的 anonymous JWT 前检查 `exp`，过期就重签。

### 附带发现

- `GET /api/users/me`（前端代理那层）**不校验** `X-API-Key`，返回 anonymous 管理员；
  不能用它解析 API Key 的身份。公开 API 是 `/api/v1/*`。
- OpenSearch 容器显示 `unhealthy` 只是 compose 健康检查里 `$$OPENSEARCH_PASSWORD`
  在该容器内未定义（只设了 `OPENSEARCH_INITIAL_ADMIN_PASSWORD`），非功能问题。
- 一把 API Key 只对应一个 OpenRAG 用户 → 只有一个 `owner`，因此
  `filters.owners` 不能拿来做多租户隔离（要每租户一个 OpenRAG 用户）；
  Phase 1 改用文件名命名空间 + `data_sources`，见
  `docs/rbac-tenant-ddd-design.md` §11。

### 检索过滤是 fail-closed 还是 fail-open

```text
filters = {"data_sources": []}   → 0 条结果（OpenRAG 内部转成 __IMPOSSIBLE_VALUE__）
filters 缺省 或 filters = {}      → 不做任何过滤，返回全库 ⚠️
```

所以外部调用方一旦漏传 `data_sources` 这个 key，就会从「查不到」直接变成「查到所有租户」。
openrag-lab 侧的处理：`RetrievalScope.filters` 永远返回带 key 的过滤器，
并且不接受客户端传入 `filters`（传了直接 422）。

### 带 `/` 的文件名会被原样保留（D1 方案的前提）

实测（2026-09-11）：multipart filename 传 `smoke-tenant/smoke-report.md`，
ingest 任务 completed，`files/get_all` 返回的 filename 一模一样；
该租户检索命中、另一租户检索为 0。

### Docling 服务：uv 缓存被清后 PDF/DOCX 全部无法入库

现象（2026-09-12，迁移存量文档时暴露）：

```text
上传 .pdf / .docx → 任务 status=completed 但 failed_files=1（任务体里没有原因）
backend 日志：Docling result unavailable after SUCCESS status:
  no existing pdf_resources_dir:
  /private/tmp/uv-cache/archive-v0/<hash>/lib/python3.13/site-packages/docling_parse/pdf_resources/
```

原因：宿主机上的 docling-serve（:5001）跑在一个 uv 缓存归档的 venv 里，
该缓存目录已经被清掉，进程还活着、`/health` 也返回 ok，但真正做转换时找不到
包内的 `pdf_resources`。

影响：`.md` / `.txt` 走不需要 Docling 的路径，不受影响；
所有 `.pdf` / `.docx` / `.xlsx` 等需要解析的格式都会失败。

处理：重启 docling-serve（必要时 `uv cache clean` 后重装 docling-parse），
然后重跑 `openrag-lab reingest-legacy` 补上失败的文档。

---

## 十、TODO

- [ ] 生成层评测：Faithfulness / Answer Relevance / Citation Accuracy
  - 用同一批问题让 Dify 和 OpenRAG 各自回答
  - 使用 DeepSeek 或人工对回答质量打分
- [ ] OpenRAG Chat/Agent 链路接入 Rerank
  - 目前 `/api/v1/search` 已支持 rerank
  - Chat/Agent 内部检索是否走同一路径需要确认并打通
- [ ] 统计显著性
  - hit@1 用 McNemar 检验
  - MRR 用 Wilcoxon 符号秩检验
- [ ] 多次运行取均值，避免单次结果偶然性
- [ ] 全量评测集自动合成一份 `fintech-all` 基线

---

## 十一、已修小项（来自 PR #10 第 4 轮评审）

PR #10（OpenAPI 契约改为生成 + 守卫）第 4 轮 AI Review 通过（78/100），
剩余两条 severity 2 已核实成立，**属于"让守卫的失败信息不说谎"**；
已在 `chore/type-check-baseline` 这个 PR 里一并修掉（各附用例）：

### 1. `documented_operations` 未过滤 path 级非方法键

- 位置：`src/openrag_lab/openapi_export.py`（函数 `documented_operations`）
- 现象：对每个 path 直接遍历其所有键并 `upper()`，而 OpenAPI 允许 path item 上出现
  `parameters` / `summary` / `description` 等非方法键 → 会产出 `PARAMETERS` 之类的伪方法
- 后果：`test_the_upstream_contract_still_exists_in_the_live_spec` 会拿伪方法去比对上游，
  报出假失败。当前 `openapi/openrag.yaml` 恰好没有这类键，故为潜在隐患
- 复现：

  ```python
  documented_operations(spec_with_path_level_parameters)
  # {'/api/v1/x': {'GET', 'PARAMETERS'}}
  ```

- 修法：只保留值为 dict 的键，与同文件的 `public_operations_in` 保持同一防护
- 已加用例：path 带 `parameters` 时不产生伪方法

### 2. 上游契约校验未处理非 200 / 非 JSON 响应

- 位置：`tests/test_openapi_artifact.py`（`test_the_upstream_contract_still_exists_in_the_live_spec`）
- 现象：前置检查只有 TCP 端口连通性，随后直接 `httpx.get(url).json()`
- 后果：端点返回 404/500 时拿到的是没有 `paths` 的 dict → **文档里每条路径都被报成 missing**
  （误导性失败，掩盖"spec 端点不可用"）；返回 HTML 错误页则直接抛 `JSONDecodeError`
- 修法：先判状态码、再判能否解析，两种情况给出明确信息（skip 并说明结论不可用）

> 两条都是 `severity 2`，不影响已合并契约的正确性；已随类型检查门禁同一个 PR 修完。


## 十二、登记表状态机 P1（2026-09-16）

设计见 `docs/document-registry-state-design.md`；本节只记 P1 落地时要紧的事。

### 为什么要做

登记表原本只有"存在/不存在"两种状态，**无法表达"正在索引"**。最坏的不一致是
"远端索引成功、本地登记失败"：那行根本不存在 → 检索看不见它（方向安全），
但它也**无法通过 API 删除**（删除必须先有登记行），只能翻日志人工发现。

### P1 改了什么

- 加 `status`（`indexing` / `indexed` / `failed`）+ `status_reason`（内部，不进 API）
- 上传顺序反转为**意图先行**：`INSERT(status=indexing) + commit` → 调 OpenRAG →
  晋升 `indexed`，失败则标 `failed`（原因落在 `status_reason`）
- **检索边界只取 `indexed`**（`list_stored_filenames`）——这是本期的核心不变量：
  "列表里能看到"与"能被检索到"从此是两件事
- 同名并发上传 / 删除在途文档 → **409**（新增 `ConflictError`）
- 迁移：`openrag-lab migrate-registry-status`（幂等；`create_all()` 不会给已有表加列）

### 实测与回归

- 本地 58 行真实数据的迁移结果：`status` 全部回填 `indexed`，二次执行输出
  "Nothing to do"（幂等）
- 189 个测试通过；6 个 P1 守卫做过变异验证（去掉边界过滤 / 去掉意图提交 /
  放宽晋升前置 / 去掉 409 映射 / 去掉删除守卫 / 破坏迁移幂等，逐个都能让测试变红）

### 两条值得记住的坑

1. **契约测试会拦住"给响应模型加字段"**：`DocumentOut` 加 `status` 后，
   `test_the_generated_contract_matches_the_committed_file` 立刻失败——按提示跑
   `openrag-lab export-openapi` 重新生成 `openrag/openrag-lab.yaml` 即可。
2. **`AsyncSession` 与并发**：写并发测试时容易顺手让多个任务共用一条 session
   （会出 SAWarning / CancelledError）。应用里是"一请求一 session"，测试也必须照做
   （阶段 0 那次也踩过同样的坑）。

---

## 十三、登记表状态机 P2：逻辑删除与墓碑（2026-09-19）

设计见 `docs/document-registry-state-design.md` §5.2 / §10.1b。P2 回答的是"删除了之后，
本地那行该怎么办"，答案是：**不删行，留墓碑**。

### 为什么是逻辑删除

P1 消灭的情形 A（远端有、本地无）有一个更根本的成因：**删行会销毁"这个文件名曾经登记过"
的唯一记录**。远端删除失败时，那行被删掉之后就再没有任何东西指向远端残留 —— 既查不到，
也无法通过 API 删除。墓碑把这个信息保住了：

- 删除是**意图先行**的：`DELETING`（提交）→ 调 OpenRAG → `DELETED` 墓碑；
- 行留在库里，用户列表**看不到**它（已删的东西再列出来会读成"删除没生效"），
  检索边界也进不去（只有 `INDEXED` 进）；
- 同名重传**复活**这一行（同 `id`、保留 `created_at`）——
  `(tenant_id, stored_filename)` 这个键从不释放。

### 三种结局，一种不留痕就会撒谎

删除调用有三种结果，P2 用 `RagOutcomeUnknownError`（端口契约，`domain/rag/ports.py`）
把它们分开，适配器负责分类（应用层不再解读 OpenRAG 的状态码）：

| 远端结局 | 本地 | 响应 |
|---|---|---|
| 删掉了 / 本来就没有（404 + `success=false, deleted_chunks=0`） | `DELETED`，有定论 | 200, `confirmed=true` |
| 超时 / 连接断（**没有定论**） | `DELETED` + `remote_outcome_unknown=true` | 200, `confirmed=false` |
| 明确拒绝（5xx 等） | 留在 `DELETING`（非终态，可被对账接手） | 502 |

第三行是"删除失败"的正常样子：**不回退成 `INDEXED`**（那是撒谎），而是停在一个能查出来的
状态。第一、二行的差别是"事实"与"猜测"，所以响应里报 `confirmed` —— 报一句光秃秃的"已删除"
就等于让猜测冒充事实。

同一条道理也用在了上传侧：入库超时会把 `FAILED` 行标成 `remote_outcome_unknown=true`。
**这是 P1 评审那条意见的正确修法** —— 评审担心"远端可能已写入，本地却是 FAILED"，
它给的建议是"失败前先探活，有就标 INDEXED"；那会把一个可能还在写、或半写坏的文档
推进检索边界（违反 I1）。正确的做法不是猜存在性，而是**把"不知道"如实记下来**。

### 状态机自己挡住比赛，不靠守卫堆叠

三种 409 全部由领域方法拒绝非法边得到，而不是在服务层加 if：

```text
mark_indexing: INDEXING/DELETING → 冲突   （上传撞在途上传 / 撞在途删除）
mark_deleting: INDEXING/DELETING/DELETED → 冲突   （删除撞在途上传 / 撞在途删除）
```

两条合起来使得"上传的晋升覆盖掉一次在途删除"**没有可达的交错**：删除不会在 `INDEXING`
时开始，上传也不会在 `DELETING` 时开始。P1 的临时守卫因此不是被删掉，而是被状态机接管。

### 契约变更（有意）

- `DELETE` 响应多一个 `confirmed` 字段（见上表）；
- 墓碑名再次删除 → **404**（它已经不是文档；未确认的重试交给对账，不走请求路径）；
- `GET /api/documents` 不返回墓碑，但返回 `deleting`（那件事还在进行中）。

### 实测与回归

- 221 个测试通过；8 个 P2 守卫做过变异验证（见 commit message 清单）；
- 迁移推广为 `openrag-lab migrate-registry`（一条命令覆盖各期，逐列判断），
  旧名 `migrate-registry-status` 保留为别名；
- 新增列 `remote_outcome_unknown`（默认 FALSE）—— 用**字段**而不是 `status_reason`
  里的措辞来表达"没有定论"，否则解析措辞的代码会在文案改动时静默失效。

### 一条值得记住的坑

**给假网关（端口替身）加能力时，别忘了替身也要跟着契约走**：端点测试里的
`FakeDocumentGateway` 实现的是**端口**，所以它现在必须返回归一化后的
`{"deleted_chunks", "already_absent"}` 或抛 `RagOutcomeUnknownError`，
不能再返回 OpenRAG 的原始 payload。适配器的分类逻辑则单独在
`tests/rag/test_openrag_gateway.py` 用假 client 测 —— 两层各自测自己那层，
替换掉任一层都不会让另一层的测试变成空转。

---

## 十四、对账报告 P3：只报告，零写入（2026-09-19）

设计见 `docs/document-registry-state-design.md` §7。命令是 `openrag-lab reconcile`。

### 它回答什么

| 报告项 | 判据 | 建议动作（**不执行**） |
|---|---|---|
| `stuck-upload` | `INDEXING` 且年龄 > 2×入库超时 | 探活：远端有 → 晋升；无 → 标失败 |
| `stuck-delete` | `DELETING` 且年龄 > 2×请求超时 | 重试远端删除（404 视为成功） |
| `failed` | 任何 `FAILED` 行 | 重传即重试；**没有定论**时先探活 |
| `unconfirmed-delete` | 墓碑 + `remote_outcome_unknown` | 重试远端删除 |
| 幽灵 | 远端有、登记表**任何状态**都没有这个名字 | 决策 5（先只报告） |
| 缺失 | 登记表说 `INDEXED`、远端没有 | 方向安全，只报 |
| 陌生命名空间 | 远端名字不属于任何租户前缀（命名空间改造前的遗留） | 归 `reingest_legacy` 处理 |
| `inconsistent` | 行带着 `remote_outcome_unknown` 却处于"该有定论"的状态 | 查写路径或库，报告不修 |

### 三条设计取舍

1. **零写入**：这一阶段一次都不写（有测试用"假网关的任何其它方法调用即失败"钉住）。
   理由：自动修复器是**权威放大器** —— 它把一个错误判断成规模地固化下去。先报告跑一段。
2. **阈值从超时推导，不是手填**：`INDEXING` 的系数 2 有具体含义（意图行在拿到限流器**之前**
   就提交了，所以行的年龄 = 排队 + 一次完整入库等待）。为此把原先硬编码在 `OpenRAGClient`
   里的 60 秒请求超时提成配置项 —— **阈值必须来自运维看得见、改得动的数字**。
3. **远端读不到时降级，而不是崩**：按租户 try/except，失败的租户记进报告并**排除在比对之外**。
   这条是在真实库上跑出来的：`OPENRAG_API_KEY` 没配时整个命令直接抛异常，而"对账"恰恰是
   出事时最需要跑的命令。更要命的是如果只崩一半 —— 未读到的租户会被误报成"缺失"，
   等于把一次超时变成一屏假警报。现在本地那一半照常输出，报告里明确写
   "读不到的租户已排除：**读不到不等于没有**"。

   评审第 1 轮又补了一刀，同一条推理仍然成立：**"配置缺失"必须与"远端故障"分开报**。
   `resolve_tenant_scope` 的 `ConfigurationError` 自述是 deployment error，被吞进
   `RemoteUnavailable` 就等于把"没配 key"说成"OpenRAG 挂了"——两者要去的地方完全不同。
   现在它单独成一类、出现在报告最上面，并且**遇到就停止遍历**（所有租户只会同样失败，
   报一次就够）。

### 实测与回归

- 263 个测试通过；ruff / mypy(94 文件) 干净；
- **变异 15/15 捕获**：阈值写死 / 边界改成 `>=` / 健康上传误报 / 已确认墓碑多报 /
  无定论按普通失败建议 / 幽灵忽略"已登记" / missing 用全部名字 / 命名空间前缀多加斜杠 /
  远端失败当空 / 失败租户仍参与比对 / 仓储多算一类 / 仓储漏谓词 / 枚举不过滤空文件名 /
  请求超时不再来自配置 / 命名空间不再自带分隔符；
- 真实库上跑过：58 行全是 `indexed` → 零待办；远端读不到时降级路径也验过（`--strict` 退出码 1）。

### 一条值得记住的坑

**"最长前缀优先"是多余的 —— 但那不是重点，重点是它掩盖了一条真不变量。**
我原本写了 `max(matches, key=len)` 并配了注释说"slug 可能互为前缀（acme vs acme-eu）"。
变异测试显示把它换成"取第一个"测试全绿：因为 `Tenant.document_namespace` **自带尾斜杠**，
`"acme/"` 根本匹配不到 `"acme-eu/x.md"` —— 前缀天然不可能重叠。
于是删掉防御代码，改成钉住那条性质本身（命名空间必须分隔符结尾）。**存活的变异体有两种：
真漏测，和"这段代码本来就没用"。分清楚再动手 —— 后者的正确动作是删除，而不是补测试。**
