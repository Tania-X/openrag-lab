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
401 未登录 / token 无效 / 调用者账号被禁用 / 登录凭证错误
403 权限不足、跨租户（非 super_admin）、租户被禁用
404 目标租户不存在
400 请求无法按原样满足（含租户文档数超过检索作用域上限）
422 请求体不合法（含传了不接受字段，如 filters）
502 调用 OpenRAG 失败
503 服务端配置缺失（例如未配置 OPENRAG_API_KEY）
```

- `401` 的**挑战头分两种**（RFC 7235，且与实现一致）：
  - 受保护接口因**缺 token / token 无效**返回的 401 → **带** `WWW-Authenticate: Bearer`
    （服务端用 `HTTPBearer(auto_error=False)` 自己产出响应，这个头也由自己带上）；
  - `POST /api/auth/login` 因**凭证错误**返回的 401 → **不带**：登录不是 Bearer 挑战，
    客户端该做的是提交正确凭证，而不是"带 token 重试"。
- **"账号被禁用"是 401，"租户被禁用"是 403**：前者说明凭证已不再指向一个有效主体
  （与 token 过期同类，重新认证是唯一出路）；后者说明主体有效、只是其作用域被关闭。
  两者都在 `get_current_user` 里判定，所有受保护接口共用。

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
      "status": "indexed",
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

`status` 是登记表的状态机（`docs/document-registry-state-design.md`）：

```text
indexing  已落本地意图, 正在调 OpenRAG —— **不进检索边界**, 列表可见
indexed   远端已确认, 正常可用 —— 只有这个状态参与 data_sources 过滤
failed    入库或晋升失败(原因留在服务端日志/登记表内部字段), 列表可见
deleting  已落删除意图, 正在等 OpenRAG —— 列表可见(删除尚未落定)
deleted   墓碑: 删除已收尾, 行**故意保留** —— 不进列表, 不进检索边界
```

也就是说：**"列表里能看到"与"能被检索到"是两件事**。未就绪的文档会出现在列表里
（否则用户会以为上传丢件），但不会进入检索边界。重传同名文件即重试（`failed` → 重新索引）。

`deleted` 是**账, 不是文档**, 所以不出现在这个列表里 —— 已经删掉的东西再列出来会读成
"删除没生效"。它保留在库里有两个原因：`(tenant_id, stored_filename)` 是登记表的键，
删行会让这个键被释放；而行本身是"这个文件名曾经登记过"的唯一记录，丢了就再也无法与远端对账。
同名重传会**复活**这一行（同一个 `id`，`created_at` 保留）。

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
3. **先落本地意图**（status=indexing + 提交），再同步等待 OpenRAG 入库任务完成；
   这样进程在任何一步崩溃, 都留下一行可被对账发现的状态, 而不是"远端有文件、本地没记录"
4. 成功后晋升 status=indexed；失败则标 status=failed（原因记在服务端）并返回错误
5. 同名重传 = 替换（OpenRAG replace_duplicates=true，登记表 update）；
   若该名字**正在索引中或正在删除中**，返回 409（等它结束再传）；
   若该名字是**墓碑**（`deleted`），则复活这一行并正常入库
```

成功返回 201 与一条 `DocumentOut`（字段同 2.5，`status` 为 `indexed`）。
状态码：400 文件名不合法或入库失败、403 无权限或跨租户、
409 该名字正被另一个操作占用（`indexing`/`deleting`）、413 超过上传上限、
502 调用 OpenRAG 失败（含**没有答复**的情况，见下）。

**"没有答复" ≠ "入库失败"**：调用 OpenRAG 超时或连接中断时，服务端并不知道远端有没有写入。
这种情况会在登记行上记一个内部标记（`remote_outcome_unknown`），供后续对账区分
"确认失败"与"结果未知"——对客户端而言仍然是 502 + `failed` 行。

**入库失败会留下 `status=failed` 的登记行**（请求本身仍然报错）。这是刻意的：
失败必须留痕、可重试、可对账，而不是只存在于日志里。该行不进检索边界。

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
未登记的文档        → 404（因此无法删除别的租户文档：那是 acme/xxx，本租户从未登记）
已是墓碑(deleted)   → 404（它已经不是文档了；未确认的删除由对账重试, 不走请求路径）
正在索引中(indexing)→ 409（避免与在途上传的晋升互相踩；等它结束再删）
正在删除中(deleting)→ 409（另一个删除正在驱动远端；等它结束）
```

删除是**意图先行**的：先把登记行置 `deleting` 并提交，再调 OpenRAG，最后落成 `deleted`
墓碑。任何一步崩溃都留下一个非终态行，对账可以接手 —— 这正是保留墓碑而不是删行的原因。

响应：

```json
{"filename": "40-2024-支付超时处理规范.md",
 "stored_filename": "default/40-2024-支付超时处理规范.md",
 "deleted_chunks": 3,
 "confirmed": true}
```

`confirmed` 是这次删除**有没有拿到远端定论**：

```text
true    远端确认了。deleted_chunks 是实际删除的分块数;
        OpenRAG 若回答"本来就没有匹配的分块", 也归入 true, deleted_chunks 为 0
false   远端超时/连接中断。文档仍算删除成功(它已经不在调用者的检索边界里),
        但它可能仍占用远端空间, 而 deleted_chunks 只能是 0 —— 那是"不知道", 不是"0 个"
```

把 `confirmed` 报出来是刻意的：一句光秃秃的"已删除"会让一个猜测冒充事实，
而调用方有权知道这次删除是否已经落定。

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

  源是 `api/main.py` 注册的全部路由 + `interfaces/schemas` 的模型。
  `security` 标注**由 FastAPI 依依赖图自动生成**：凡是经 `get_current_user`（含
  `require_permission` 间接依赖）保护的操作都带 `security: [{HTTPBearer: []}]`，
  公开三件套（`/api/health`、`/api/auth/register`、`/api/auth/login`）不带。
  期望的公开面在 `openapi_export.PUBLIC_OPERATIONS` 里以 **(METHOD, path)** 显式声明，
  测试会把它与「依赖图推导出的受保护集合」以及生成物三方对齐——
  即：声明、代码接线、文档任意两者不一致都会失败。
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
