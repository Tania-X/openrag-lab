# OpenRAG Lab Agent Instructions

These rules apply to any coding agent working in this repository.

## Git / PR Rules

- Do NOT push directly to `main`.
- Always develop on a `feat/*` branch and open a Pull Request.
- Never delete `feat/*` branches after merging.
- Merge Pull Requests with a regular merge commit, never squash.
- Follow the `pr-ai-review-loop` skill from the skills-hub repository:
  - https://github.com/Tania-X/skills-hub
  - skill path: `skills/pr-ai-review-loop/SKILL.md`
  - If AI Review reports severity >= 4, fix and push, then monitor again.
  - If severity < 4, stop and ask the human for a decision.

## Git 分支纪律（每次 git 操作前必读）

> 本仓发生过多次"在错误的分支上提交 / 改写历史"的事故（`--amend` 落到 `main`、stash 恢复到
> 错误分支、PR 与 main 冲突导致 workflow 根本不触发）。规则只有一句：**动手前确认分支，
> 动完再确认一次。**

- **每次 git 操作前先跑 `git branch --show-current`**，单独一条命令，不要依赖上一条命令留下的
  状态。提交、push、rebase、cherry-pick、`stash pop`、`--amend` 之前都要确认目标分支。
- **一条命令链里出现 `checkout` 之后，其后每个 git 操作前都必须重新确认分支**：`checkout` 可能
  因未提交改动而失败（`&&` 链会中断，但换成 `;` 或分条执行就不会），于是后续操作落在旧分支上。
  真实事故：`git checkout main` 失败 → 链尾的 `git commit --amend` 改写了 **main**。
- **`git push origin <branch>` 推的是本地分支 ref，不是 HEAD**：以为在推 A、实际 HEAD 在 B 时，
  命令仍会"成功"却什么也没推。push 前确认 `git branch --show-current` 与目标分支一致。
- **开 PR 前先 rebase 到最新 `origin/main`**（`git fetch origin && git rebase origin/main`）：
  PR 与 main 冲突时 GitHub 无法生成 `refs/pull/<n>/merge`，**`pull_request` 类 workflow 不会触发**
  ——表现为"没有 CI、没有 AI Review"而不是报错。push 后用 PR 的 `mergeable` 字段确认。
- **不要基于过期的 `main` 改文件**：分支建于旧 main、期间 main 又合并了改同一文件的 PR，会造成
  冲突 + 重复改动。开始改之前 `git fetch origin && git log --oneline origin/main -3` 确认基线。
- **向显式 URL 推送时 `--force-with-lease` 必须写明期望值**：
  `git push --force-with-lease=<branch>:<expected-sha> <url> <branch>`；否则 git 查不到跟踪引用，
  直接以 `stale info` 拒绝。
- **用带 token 的 URL 推送后把 remote 改回普通 URL**（`git remote set-url origin https://...`），
  别让凭据留在 `.git/config`。
- **提交前看清单**：`git status --short` + `git diff --stat`，确认暂存内容属于当前任务
  （多任务并行时 stash / 临时分支 / 合并模拟最容易串味）。
- **临时分支用完即删**（`git branch -D tmp/...`）：合并模拟、验证用分支不要留在本地造成混淆。

速查：**事故 → 规则**

| 发生过的事故 | 对应的规则 |
|---|---|
| `--amend` 落到 `main`（`checkout` 静默失败） | 命令链里有 `checkout` 时，其后每个操作前重新确认分支 |
| stash 恢复到错误分支、遗留 stash | 操作前确认分支；用完检查 `git stash list` |
| push "成功"但其实没推（HEAD 不在目标分支） | push 前确认 `git branch --show-current` == 目标分支 |
| PR 与 main 冲突 → 没有 CI / 没有 AI Review | 开 PR 前 rebase 到最新 `origin/main`，并看 `mergeable` |
| 基于旧 main 改同一文件 → 冲突 + 重复改动 | 动手前 fetch 并看 `origin/main` 最新提交 |
| `--force-with-lease <url>` 报 `stale info` | 写明 `<branch>:<sha>` 期望值 |
| token 留在 `.git/config` | 推完 `git remote set-url` 还原 |
| 临时分支/合并模拟残留，串到别的工作里 | 用完即删 |

## Docs

- Project progress and pitfalls: `docs/progress-and-pitfalls.md`
