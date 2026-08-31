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
│   ├── client.py       # OpenRAG API 客户端
│   ├── ingest.py       # 文档入库
│   ├── metadata.py     # 元数据/知识过滤
│   ├── eval.py         # 评测脚本
│   └── migrate.py      # Dify -> OpenRAG 迁移辅助
├── scripts/            # 运维/初始化脚本
├── configs/            # OpenRAG 环境与流程配置
├── data/               # 本地数据（gitignored）
└── docs/               # 设计文档
```

## 快速开始

```bash
uv sync
cp .env.example .env
# 编辑 .env 填写 OpenRAG 地址与 API Key

uv run openrag-lab init
uv run openrag-lab ingest --help
uv run openrag-lab eval --help
```

## 当前状态

- [x] 项目骨架
- [ ] OpenRAG 服务启动
- [ ] 文档入库
- [ ] 元数据适配
- [ ] 评测集迁移
- [ ] Dify vs OpenRAG 对比
