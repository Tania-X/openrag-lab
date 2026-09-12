# OpenRAG Lab API 契约

> 本文档定义 OpenRAG Lab 的接口约定，分为两层：
> 1. 自研前端 ↔ OpenRAG Lab 后端（FastAPI）
> 2. OpenRAG Lab 后端 ↔ OpenRAG 服务（Public API）

---

## 1. 总体架构

```text
React 前端
   ↓ HTTP / JSON
FastAPI 后端（openrag-lab）
   ↓ HTTP / JSON
OpenRAG Public API（/api/v1/*）
   ↓
OpenSearch / Langflow / new-api
```

---

## 2. 自研前端 ↔ OpenRAG Lab 后端

### 2.1 通用约定

- Base URL：`http://localhost:8000`
- Content-Type：`application/json`
- 错误响应统一结构：

```json
{
  "detail": "错误信息"
}
```

- 认证（s1p2 起）：除 `/api/health`、`/api/auth/register`、`/api/auth/login` 外，
  所有接口都要求 `Authorization: Bearer <token>`，并且按 RBAC 校验权限。
- 状态码约定：

```text
401 未登录 / token 无效
403 权限不足、跨租户（非 super_admin）、租户被禁用
404 目标租户不存在
400 请求无法按原样满足（含租户文档数超过检索作用域上限）
422 请求体不合法（含传了不接受字段，如 filters）
502 调用 OpenRAG 失败
503 服务端配置缺失（例如未配置 OPENRAG_API_KEY）
```

- 「租户被禁用」对所有需要认证的接口都成立：调用者自己所属租户被禁用时，
  即使它是全局 `super_admin` 也不能继续跨租户读取（校验在建立身份的
  `get_current_user` 里完成，所有受保护接口共用）。
- **租户边界由服务端决定**：`/api/search`、`/api/chat` 不接受客户端传 `filters`，
  服务端按调用者租户的登记文档生成 `data_sources` 过滤；
  响应里的 `scope` 回显本次实际边界，便于前端展示与审计。
- 租户文档数上限：`data_sources` 就是该租户的登记文件名列表，
  超过 `MAX_SCOPED_DOCUMENTS`（当前 5000）时返回 400 并说明原因，
  而不是把超大请求体发给 OpenRAG 换回一个不明所以的 502；
  超过 500 条时服务端记 warning 日志。
- 错误顺序约定（`/api/search`、`/api/chat`）：跨租户请求**先判「能不能跨」，再看租户是否存在**。

```text
非 super_admin 传一个不存在的 tenant_id → 403（而不是 404）
super_admin    传一个不存在的 tenant_id → 404
```

  这是刻意选择：不向「本来就无权跨租户」的调用者暴露任意租户是否存在（避免租户存在性探测）。
  例外：`POST /api/users` 沿用 s1p2 的顺序（先查租户 → 404，再判跨租户 → 403），
  两处差异留待后续阶段统一。
- 注意：OpenRAG 外部服务认证是独立的一层，必须携带 `X-API-Key`，见第 3 节。

### 2.2 GET /api/health

健康检查。

响应：

```json
{
  "status": "ok"
}
```

### 2.3 POST /api/search

需要权限：`search:use`。返回调用者租户范围内的检索结果。

请求：

```json
{
  "query": "支付网关读超时",
  "limit": 10,
  "score_threshold": 0,
  "rerank": true,
  "rerank_model": "BAAI/bge-reranker-v2-m3",
  "rerank_top_n": 10,
  "tenant_id": null
}
```

- 不接受 `filters`：传了直接 422（避免调用方误以为自己的过滤生效）
- `tenant_id` 只有 `super_admin` 可传；不传时一律限定调用者自己的租户

响应：

```json
{
  "results": [
    {
      "filename": "acme/40-2024-支付超时处理规范.md",
      "text": "...",
      "score": 0.99,
      "page": 0,
      "mimetype": "text/markdown"
    }
  ],
  "scope": {
    "tenant_id": "8c309296-19d3-46f0-b61f-0fa42e0e5b14",
    "document_count": 1,
    "cross_tenant": false
  }
}
```

### 2.4 POST /api/chat

需要权限：`chat:use`。与 search 同一套租户边界。

请求：

```json
{
  "message": "2024 年支付网关读超时是多少？",
  "limit": 10,
  "score_threshold": 0,
  "tenant_id": null
}
```

响应：

```json
{
  "response": "2024 年支付网关读超时为 5 秒。",
  "scope": {
    "tenant_id": "8c309296-19d3-46f0-b61f-0fa42e0e5b14",
    "document_count": 1,
    "cross_tenant": false
  }
}
```

### 2.5 GET /api/documents

需要权限：`documents:read`。只返回调用者租户**已登记**的文档
（登记表 `documents` 同时是检索边界的来源）。

```json
{
  "total": 1,
  "files": [
    {
      "id": "0f1c…",
      "display_name": "40-2024-支付超时处理规范.md",
      "stored_filename": "default/40-2024-支付超时处理规范.md",
      "mimetype": "text/markdown",
      "size_bytes": 1709,
      "openrag_document_id": "ONjbpbZ-8UkjaTkuX_FbUV9D",
      "uploaded_by": "…",
      "created_at": "2026-09-12T…",
      "updated_at": "2026-09-12T…"
    }
  ]
}
```

`tenant_id` 是**可选查询参数**，只有 `super_admin` 能用（同 search/chat 的规则）。

`openrag_document_id` 是 OpenRAG 侧的文档 id：入库任务本身不回传它，
服务端在入库成功后回查一次（best effort，查不到就是 `null`），
同名重传换了内容会刷新成新 id。

### 2.6 POST /api/documents/ingest

需要权限：`documents:upload`。`multipart/form-data`，字段：

```text
file       必填（multipart），文件本体；默认上限 50 MiB，见 MAX_UPLOAD_BYTES
tenant_id  可选（multipart 表单字段），只有 super_admin 可用
```

接受的格式（与 CLI 入库同一份白名单，见 `domain/rag/documents.py`）：

```text
.md .txt .pdf .docx .xlsx .csv .html .htm
```

其他后缀在触达 OpenRAG 之前就被拒（400），不会送进解析器。

规则：

```text
1. 客户端文件名先被降为 basename（浏览器会送 C:\fakepath\x.pdf）
2. 再由领域规则生成存储名 <slug>/<basename>：
   含路径分隔符、以点开头、超长的名字一律 400
3. 同步等待 OpenRAG 入库任务完成；失败/超时不写登记表
4. 同名重传 = 替换（OpenRAG replace_duplicates=true，登记表 update）
5. 上传成功才登记，因此登记表里不会出现没索引成功的文档
```

成功返回 201 与一条 `DocumentOut`（字段同 2.5）。状态码：400 文件名不合法或入库失败、
403 无权限或跨租户、413 超过上传上限、502 调用 OpenRAG 失败。

上传是**长事务**：请求会同步等待 OpenRAG 的入库任务完成，等待上限由
`UPLOAD_INGEST_TIMEOUT_SECONDS` 控制（默认 300 秒），期间占用一个工作线程。
并发上传多份大文件会挤占同一进程的线程池，从而拖慢 search/chat；
需要大批量入库时用 CLI 或分批提交，不要并发打这个接口。

### 2.7 DELETE /api/documents/{filename}

需要权限：`documents:delete`。路径参数是**显示名**（不是存储名），
服务端据此推导存储名并在登记表里校验归属。

`tenant_id` 是可选查询参数，作用域规则与 search/chat 完全一致（由同一个
`resolve_tenant` 决定）：

```text
不传 tenant_id        → 只能删自己租户已登记的文档
传别人的 tenant_id    → 非 super_admin 一律 403
                        super_admin 可以删任意 ACTIVE 租户的文档
                        （全局根角色，与它可以在任意租户建用户同一套授权模型）
```

```text
未登记的文档 → 404（因此无法删除别的租户文档：那是 acme/xxx，本租户从未登记）
```

响应：

```json
{"filename": "40-2024-支付超时处理规范.md",
 "stored_filename": "default/40-2024-支付超时处理规范.md",
 "deleted_chunks": 3}
```

---

## 3. OpenRAG Lab 后端 ↔ OpenRAG 服务

OpenRAG Lab 通过 OpenRAG 的公开 API 与 OpenRAG 服务通信。

- Base URL：`http://localhost:3000`
- 认证：`X-API-Key: orag_...`

### 3.1 POST /api/v1/search

OpenRAG 原生检索接口，已支持 rerank。

请求：

```json
{
  "query": "支付网关读超时",
  "limit": 10,
  "score_threshold": 0,
  "rerank": true,
  "rerank_model": "BAAI/bge-reranker-v2-m3",
  "rerank_top_n": 10,
  "filters": {
    "data_sources": ["40-2024-支付超时处理规范.md"]
  }
}
```

响应：

```json
{
  "results": [
    {
      "filename": "...",
      "text": "...",
      "score": 0.99,
      "page": 0,
      "mimetype": "text/markdown"
    }
  ]
}
```

### 3.2 POST /api/v1/chat

OpenRAG 原生 Chat 接口。

请求：

```json
{
  "message": "...",
  "stream": false,
  "limit": 10,
  "score_threshold": 0
}
```

响应：

```json
{
  "response": "...",
  "chat_id": "...",
  "sources": []
}
```

### 3.3 POST /api/v1/documents/ingest

文档入库接口，`multipart/form-data`。

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `file` | file | 待入库文件 |
| `replace_duplicates` | string | 是否替换重复文件，传字符串 `"true"` / `"false"`，默认 `"true"` |

响应：

```json
{
  "task_id": "...",
  "message": "Langflow upload task created for 1 file(s)",
  "file_count": 1
}
```

### 3.4 GET /api/v1/tasks/{task_id}

查询入库任务状态。

响应：

```json
{
  "task_id": "...",
  "status": "completed",
  "total_files": 1,
  "processed_files": 1,
  "successful_files": 1,
  "failed_files": 0
}
```

### 3.5 GET /api/v1/files/get_all

获取已入库文件列表。

响应：

```json
{
  "files": [],
  "total": 0
}
```

---

## 4. OpenAPI 文件

- `openapi/openrag-lab.yaml`：自研前端 ↔ OpenRAG Lab 后端契约
  **由代码生成，请勿手工修改**：

  ```bash
  openrag-lab export-openapi        # 等价于 openrag-lab export-openapi -o openapi/openrag-lab.yaml
  ```

  源是 `api/main.py` 注册的全部路由 + `interfaces/schemas` 的模型；
  除 `/api/health`、`/api/auth/register`、`/api/auth/login` 外，所有操作都标注
  `security: [{HTTPBearer: []}]`（公开端点在 `openapi_export.PUBLIC_OPERATIONS` 里显式声明）。
  `tests/test_openapi_artifact.py` 会重新生成并比对，**契约与代码不一致时 CI 直接失败**——
  这是为了避免它再次变成"手工维护然后悄悄过期"（曾出现只列 4 条路由、而实际有 13 条的阶段）。

- `openapi/openrag.yaml`：OpenRAG Lab 后端 ↔ OpenRAG Public API 子集
  这份是**手工维护**的（我们只消费上游 121 条路径里的 6 条，全量生成没有意义），
  但同一个测试文件会在 OpenRAG 可达时校验：这里写的每个 path/method 都必须存在于
  线上 spec（`{OPENRAG_BASE_URL}/api/openapi.json`）——上游改名/删端点会让测试失败。
  注意上游 spec 的路径不带 `/api` 前缀（`/v1/search`），我们调用时走代理的 `/api/v1/search`。

## 5. 演进约定

- 前后端联调时以本文档为准
- 后续新增接口必须在本文档和对应 OpenAPI 文件中同步更新
- 所有 API 变更走 feature branch + PR + AI Review
