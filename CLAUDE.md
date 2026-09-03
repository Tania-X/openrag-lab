# OpenRAG Lab 协作规则

本文件用于约束 Claude Code / AI 编程助手在本仓库内的操作习惯。

## PR / 分支规则

- 功能开发必须走 feature 分支，禁止直接 push 到 main。
- feature 分支合并后**永不删除**，保留完整历史。
- 合并 PR 使用普通 merge，**不要 squash merge**。
- AI Review 流程遵循 `ai-review-loop`：
  - severity >= 4：自动修复 → push → 继续监听。
  - severity < 4：停下，分析并交给人决策。

## 参考

- 本地 skill：`/Users/apple/dsh/skills/ai-review-loop.md`
- 项目文档：`docs/progress-and-pitfalls.md`
