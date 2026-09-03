# OpenRAG Lab

围绕 OpenRAG 构建的金融知识库工程实验仓库。

目标：

- 使用 Python 管理 OpenRAG 的接入、文档入库、元数据、评测与迁移。
- 将 `dify-rag-lab` 中验证过的数据资产、评测集和检索策略适配到 OpenRAG。
- 用同一套评测集对比 Dify 与 OpenRAG，形成数据驱动的技术选型依据。

## 项目结构

```text
openrag-lab/
├── pyproject.toml
├── src/openrag_lab/
│   ├── cli.py          # 命令行入口
│   ├── config.py       # 配置加载
│   ├── client.py       # OpenRAG v1 API 客户端
│   ├── ingest.py       # 文档入库
│   ├── metadata.py     # Dify 元数据 -> OpenRAG 过滤映射
│   ├── eval.py         # 评测逻辑（hit@1 / hit@k / MRR）
│   └── migrate.py      # Dify -> OpenRAG 迁移辅助
├── configs/eval/       # 从 dify-rag-lab 同步的评测集
├── data/               # 本地数据（gitignored）
└── docs/               # 设计文档与踩坑记录
```

## 快速开始

```bash
uv sync
cp .env.example .env
# 编辑 .env 填写 OpenRAG 地址与 API Key

uv run openrag-lab init
uv run openrag-lab list-files
uv run openrag-lab ingest --directory data/sample-data
uv run openrag-lab eval --csv configs/eval/评测集-questions.csv --top-k 5
```

## 已对齐的 OpenRAG API

`client.py` 已经对接 OpenRAG v0.7.1 的公开 v1 API：

- `GET /api/health`
- `POST /api/v1/search`
- `POST /api/v1/chat`
- `POST /api/v1/documents/ingest`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/files/get_all`
- `POST /api/v1/knowledge-filters`

## 元数据适配策略

OpenRAG 的 search filter 是：

```text
data_sources      -> 文件名列表
document_types    -> MIME 类型列表
owners            -> 属主
connector_types   -> 连接器类型
```

Dify 的 `year` / `version` 元数据在 OpenRAG 中更适合映射为 `data_sources`，
即按文件名筛选（例如 `40-2024-支付超时处理规范.md`）。

```bash
uv run openrag-lab eval --csv configs/eval/fintech-metadata-year-评测集-questions.csv --use-metadata
```

## 当前状态

- [x] 项目骨架
- [x] OpenRAG 服务启动
- [x] 文档入库
- [x] 元数据适配
- [x] 评测集迁移
- [ ] Dify vs OpenRAG 对比

## Dify vs OpenRAG 对比

```bash
# 基础检索对比（两边都不开 rerank / metadata / rewrite）
uv run openrag-lab compare --csv configs/eval/fintech-评测集-questions.csv --top-k 5

# Dify 开启 Rerank 后对比
uv run openrag-lab compare --csv configs/eval/fintech-评测集-questions.csv --top-k 5 --dify-rerank

# OpenRAG 开启 Rerank 后对比
uv run openrag-lab compare --csv configs/eval/fintech-评测集-questions.csv --top-k 5 --openrag-rerank

# 两边都启用元数据过滤
uv run openrag-lab compare \
  --csv configs/eval/fintech-metadata-year-评测集-rewrite-ab.csv \
  --top-k 5 --use-metadata

# 用 rewritten_query 做 Query Rewrite A/B
uv run openrag-lab compare \
  --csv configs/eval/fintech-metadata-year-评测集-rewrite-ab.csv \
  --top-k 5 --query-field rewritten_query
```

`compare` 命令会输出同一评测集下 Dify 与 OpenRAG 的：

```text
hit@1 / hit@k / MRR
```

> 注意：当前 Dify 知识库是 50 份金融文档，OpenRAG 是 58 份（包含额外通用文档）。
> 直接对比通用研发运维评测集会不公平；应使用两边都覆盖的金融评测集。

## 一键生成全量对比报告

```bash
uv run openrag-lab compare-all --output docs/comparison-report.md
```

会跑一遍内置的 Dify vs OpenRAG 检索对比矩阵，并生成 Markdown 报告。

## Web 前后端（FastAPI + React）

当前已搭建最小可运行骨架：

```text
src/openrag_lab/api/     # FastAPI 后端
frontend/                # React + Vite + TypeScript 前端
```

### 启动后端

```bash
uv sync
uv run uvicorn openrag_lab.api.main:app --reload --port 8000
```

### 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端开发服务器默认：

```text
http://localhost:5173
```

它会将 `/api` 代理到：

```text
http://localhost:8000
```

当前页面：

- Chat
- Search
- Documents

后续会逐步接入知识库管理、评测、对比报告等能力。

## API 契约

接口约定与 OpenAPI 文件：

```text
docs/api-contract.md              # 前后端 + OpenRAG 外部服务契约说明
openapi/openrag-lab.yaml          # 自研 FastAPI 后端 OpenAPI
openapi/openrag.yaml              # OpenRAG Public API 调用子集
```
