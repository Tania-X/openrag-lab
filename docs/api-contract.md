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

- 认证：当前阶段未强制；后续如加入用户体系，统一通过 `Authorization: Bearer <token>`。

### 2.2 GET /api/health

健康检查。

响应：

```json
{
  "status": "ok"
}
```

### 2.3 POST /api/search

调用 OpenRAG 检索，并透传 rerank 等参数。

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
      "filename": "40-2024-支付超时处理规范.md",
      "text": "...",
      "score": 0.99,
      "page": 0,
      "mimetype": "text/markdown"
    }
  ]
}
```

### 2.4 POST /api/chat

调用 OpenRAG Chat。

请求：

```json
{
  "message": "2024 年支付网关读超时是多少？",
  "limit": 10,
  "score_threshold": 0,
  "filters": {
    "data_sources": ["40-2024-支付超时处理规范.md"]
  }
}
```

响应：

```json
{
  "response": "2024 年支付网关读超时为 5 秒。",
  "chat_id": "1e794331-3555-479b-84c5-0ef7ece6149a",
  "sources": []
}
```

### 2.5 GET /api/documents

获取当前 OpenRAG 知识库文件列表。

响应：

```json
{
  "total": 58,
  "files": [
    {
      "filename": "40-2024-支付超时处理规范.md",
      "document_id": "xxx",
      "mimetype": "text/markdown",
      "chunk_count": 3,
      "embedding_model": "BAAI/bge-m3"
    }
  ]
}
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
| `replace_duplicates` | string | 是否替换重复文件，默认 `true` |

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
- `openapi/openrag.yaml`：OpenRAG Lab 后端 ↔ OpenRAG Public API 子集

## 5. 演进约定

- 前后端联调时以本文档为准
- 后续新增接口必须在本文档和对应 OpenAPI 文件中同步更新
- 所有 API 变更走 feature branch + PR + AI Review
