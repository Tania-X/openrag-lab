# 登记表状态机设计（草案 · 待 review）

> 状态：**P1 已实现**（2026-09-16，决策见 §10）。P2/P3/P4 未开始。
> 相关文档：`docs/rbac-tenant-ddd-design.md`（D1 隔离）、`docs/api-contract.md`（文档契约）、
> `docs/progress-and-pitfalls.md`（迁移事故记录）。
> 来源讨论：本仓 issue #12 的可行性分析里提到"跨服务一致性"；本文是它的落地设计。

## 1. 一句话

登记表（`documents`）目前只有"存在/不存在"两种状态，**无法表达"正在索引"**，所以
一旦外部调用与本地提交之间出错，不一致只能靠日志人工发现、无法自动修复。
本设计给它加一个**受控状态机 + 对账任务**，把"不一致"变成"一行自描述的记录"。

**这不是分布式事务**。OpenRAG 是普通 HTTP 服务，没有 prepare/commit 协议，无法纳入 2PC；
这里走的是工业界的常规做法：本地意图 + 幂等重放 + 对账修复（最终一致）。

## 2. 现状：四种不一致，以及它们各自的方向

当前写路径（`application/rag/document_service.py`）：

```text
上传: 校验 → 调 OpenRAG ingest(等待完成) → 写登记表 + commit   (先外部, 后本地)
删除: 查登记表 → 调 OpenRAG delete        → 删登记行 + commit   (先本地查, 再外部, 再本地删)
```

`_commit_registry` 已经做对了三件事：回滚本地事务、带上下文大声记录、**重新抛出**。
它的问题是**只记录不修复**：失败留一条日志，然后就没有然后了。

由此产生的不一致（按危害排序）：

| # | 情形 | 谁受影响 | 现状能否发现 | 能否自愈 |
|---|---|---|---|---|
| A | 远端索引成功，本地登记失败（进程崩溃 / DB 故障） | 远端多一个文件，**登记表没有它** → 检索看不见它（方向安全），但它**无法通过 API 删除**（删除必须先有登记行） | 只能靠日志 | ❌ |
| B | 本地登记成功，远端其实失败（例如 `_ensure_ingested` 被绕过、或远端后续丢了文档） | 检索边界里有一个不存在的名字（多一个过滤器值，匹配不到，**安全**），列表里却显示它存在 | 无 | ❌ |
| C | 替换上传：远端已是新内容，本地 `openrag_document_id` 没更新 | 列表/Chat 引用的 id 与远端不一致 | 无 | ❌ |
| D | 并发同名上传（**阶段 0 测试实测**） | 两个请求都查到"不存在"→ 都 INSERT → 第二个收到 `AlreadyExistsError`（现被映射成 400） | 用户看到 400 | ❌（但至少没写坏数据，`UNIQUE(tenant_id, stored_filename)` 挡住了） |

D 这条是我在做阶段 0 的并发测试时撞出来的，说明**幂等约束已经在工作**，但用户拿到的 400
语义不清（"已存在"更像是 409，或者应该被识别为"正在索引中"）。

## 3. 不变量（设计要守住的东西）

- **I1 检索边界只含已就绪文档。** `list_stored_filenames()` 只返回 `INDEXED` 行。
  未就绪的文档绝不能进入 `filters["data_sources"]`——这直接决定隔离语义，方向必须是"宁可搜不到"。
- **I2 每行都处于一个已知状态**，且状态只能按 §4 的边迁移（没有"隐式状态"）。
- **I3 外部调用可安全重放。** 同一 `(tenant_id, stored_filename)` 重复 ingest 必须幂等
  （OpenRAG 侧靠 `replace_duplicates=true`，本地靠已有唯一约束）。
- **I4 任何不一致都可被查询发现**，不依赖日志考古：非终态行本身就是"待处理清单"。
- **I5 用户可见语义不变**：上传成功 = 可被检索；删除成功 = 检索不到且列表里消失。

## 4. 状态空间与迁移

```
                 ┌──────────────┐
   upload 开始 → │  INDEXING    │ ── ingest 成功 ──→ ┌───────────┐
                 └──────┬───────┘                    │  INDEXED  │ ←──┐
                        │                            └─────┬─────┘    │
                  ingest 失败 / 超时                      │ delete   │ 对账判定远端仍在
                        ↓                                 ↓          │
                 ┌──────────────┐                  ┌────────────┐    │
                 │   FAILED     │                  │  DELETING  │────┘
                 └──────┬───────┘                  └─────┬──────┘
                        │ 重试(upload)                   │ 远端删除成功(或 404 视为已删)
                        └──────────→ INDEXING            ↓
                                                    行被删除(终态)
```

| 状态 | 含义 | 是否进检索边界 | 是否出现在 `GET /api/documents` |
|---|---|---|---|
| `INDEXING` | 已落本地意图，外部索引进行中 | ❌ | 决策点 2（建议：出现，带状态） |
| `INDEXED` | 远端已确认，正常可用 | ✅ | ✅ |
| `FAILED` | 远端失败或对账判定不一致，等待人工/重试 | ❌ | 决策点 2（建议：出现，带状态） |
| `DELETING` | 正在删除（远端已发起，本地行待删） | ❌ | 决策点 2（建议：出现，带状态） |

**为什么 `DELETING` 需要单独一个状态**：删除失败的补偿方向是"重试删除"，而不是"恢复可用"。
如果只用一个 `DELETING` 标记，对账时就能一眼区分"这次删除卡住了"与"这个文档正常"。
另一个选择是"删除时直接从 `INDEXED` 跳到行删除、失败就靠远端 404 容忍"——见决策点 7。

## 5. 写路径改造（逐处落点）

### 5.1 上传（`DocumentService.upload_document`）

```text
1. 解析租户 + 校验文件名（不变）
2. find_by_stored_filename(tenant, stored)
   ├─ 存在且 INDEXED   → 走"替换"路径(见 5.3)
   ├─ 存在且 INDEXING  → 直接返回 409「正在索引中」（决策点 2 配套）
   └─ 不存在           → INSERT(status=INDEXING) + **commit**   ← 新增的第一次提交
3. 调 OpenRAG ingest（阻塞，走 INGEST limiter）+ _ensure_ingested
4. UPDATE status=INDEXED, openrag_document_id=... , touch() + commit
   若 3/4 失败 → UPDATE status=FAILED, 记录原因 + commit（不再留下幽灵）
```

两个关键变化：

- **多了一次提交**（第 2 步）。这一步把"意图"先落盘，于是进程崩溃后**有据可查**：
  对账任务能看到一行 `INDEXING` 超时的记录，而不是什么都没有（消灭情形 A 的"不可见"）。
- **失败不再只写日志**：第 3 步失败会把行标成 `FAILED`，对账/人工都能看到（情形 A/B 收敛）。
  注意顺序：`FAILED` 的写入本身也可能失败——那时留下的还是 `INDEXING`，仍然可被对账发现 ✓
  （两个状态都是"非终态"，所以任何一条崩溃路径都落在"可发现"的一侧，这是设计上的兜底）。

### 5.2 删除（`DocumentService.delete_document`）

```text
1. find_by_stored_filename → 不存在或已是墓碑 → 404
2. UPDATE status=DELETING + commit
3. 调 OpenRAG delete, 结局三分:
     有定论(删掉了 / 远端本来就没有)     → 4
     没有定论(超时/连接断)               → 4, 但记 remote_outcome_unknown
     明确拒绝(5xx 等)                    → 留在 DELETING, 502(对账重试)
4. UPDATE status=DELETED + status_reason + commit   ← 墓碑, **不删行**
   若 4 失败 → 仍是 DELETING（重试删除）
```

与草案的差异：第 4 步**不删行**（原写 "DELETE 行"）。删行会释放
`(tenant_id, stored_filename)` 这个键，也会销毁"这个文件名曾经登记过"的唯一记录 ——
而那条记录正是删除失败后唯一能让对账找到远端残留的东西（§2 情形 A 的教训）。

### 5.3 替换上传（已 `INDEXED` 的行再上传同一文件名）

保持"行不变、内容替换"的语义，但顺序改为：

```text
1. UPDATE status=INDEXING(保留 openrag_document_id 与 created_at) + commit
2. 调 ingest(replace_duplicates=true) + _ensure_ingested
3. UPDATE status=INDEXED, 元数据(display_name/mimetype/size/uploaded_by/updated_at) + commit
```

这样"替换期间"该文档会短暂退出检索边界（`INDEXING`），代价是替换过程中的检索少一个文档，
收益是**不会检索到"替换了一半"的状态**。这是个取舍，列为决策点 6。

## 6. 读路径改造

| 位置 | 现在 | 改为 |
|---|---|---|
| `SqlDocumentRepository.list_stored_filenames()`（检索边界） | 返回全部 | **只返回 `INDEXED`**（I1，硬要求） |
| `SqlDocumentRepository.list_by_tenant()`（API 列表） | 返回全部 | 决策点 2：要么只返回 `INDEXED`（最简单、契约不变），要么带出 `status` 让前端显示"索引中" |
| `RetrievalScopeResolver.resolve()` 的 `MAX_SCOPED_DOCUMENTS=5000` 判断 | 计数全部 | 计数只算 `INDEXED`（否则索引中的行会虚增边界大小） |

## 7. 对账任务（reconciliation）

设计成一个**CLI 子命令**（沿用 `reingest_legacy` 的形态：`typer` 命令 + `asyncio.run`），
而不是 lifespan 后台任务——理由：它需要显式可控（先 report 后 repair）、不能拖慢启动、
且未来可以挂 cron。

```text
openrag-lab documents reconcile [--tenant <slug>] [--fix] [--age-minutes 30]
```

三类输入与动作：

| 输入 | 判定 | 动作（`--fix` 时） |
|---|---|---|
| `INDEXING` 且 `updated_at` 早于 age 阈值 | 可能崩溃在上传中途 | 用 `find_document_id(tenant, stored)` 复核：**远端有** → 提升为 `INDEXED`；**远端无** → 标 `FAILED`（或重试 ingest） |
| `DELETING` 且超过年龄阈值 | 删除卡住 | 重试远端删除（404 视为成功）→ 删本地行 |
| 远端有、本地无（幽灵，按命名空间前缀枚举） | 情形 A 的遗留 | 决策点 5：自动登记 / 自动删除 / 只报告 |

实现要复用的两点：`client.list_files()`（远端枚举，一次拉全量再按前缀分组，
而不是逐个 `find_document_id`）与 `sweep_stale_uploads` 的**年龄守卫 + 容忍竞态**风格。

## 8. 迁移：新列怎么落到已有库

**这是本项目的一个硬约束**，见 `infrastructure/db/session.py:50-57` 的 docstring：

> Existing tables are left exactly as they are … Columns added to an existing table are
> **NOT** applied here; Alembic is the long-term answer for those.

也就是说 `create_all()` 不会加列，必须显式处理。三个选项：

| 选项 | 做法 | 代价 |
|---|---|---|
| **M1 手写一次性迁移命令**（已采用） | `openrag-lab migrate-registry`：检查列是否存在 → `ALTER TABLE documents ADD COLUMN ...` | 需要自己维护"幂等 + 可重入"；**已按列判断, 一条命令覆盖各期**, 不必每期新增命令 |
| M2 现在引入 Alembic | 正规化 schema 版本管理 | 一次性成本高（配置、基线 revision、CI），但长期收益明确 |
| M3 先不管存量库 | 只保证新库正确 | ❌ 不可接受：本地库有 58 行真实数据，升级后会 500 |

存量数据的默认值选择：**`DEFAULT 'indexed'`**。
因为迁移前的每一行都是"已登记且已索引"（`_ensure_ingested` 保证过），这是唯一诚实的默认值。

## 9. 测试策略

| 层次 | 用例 | 守护的不变量 |
|---|---|---|
| 领域单测 | `Document` 状态方法只允许合法迁移；非法迁移抛 `InvalidOperationError` | I2 |
| 仓储单测 | `list_stored_filenames` 不含 `INDEXING`/`FAILED`/`DELETING` | **I1（最关键）** |
| 服务集成 | 上传成功 → 行终态 `INDEXED`；ingest 失败 → `FAILED`；ingest 成功但 commit 失败 → 行仍是 `INDEXING`（可被对账发现） | I3/I4 |
| 崩溃注入 | 在 `_commit_registry` 处注入异常，跑对账，断言收敛到正确状态 | I4 |
| 替换/删除 | 替换期间不进检索边界；删除失败留在 `DELETING` | I1/I5 |
| 并发 | 同名并发上传：一个成功、另一个得到 409（而不是 400/IntegrityError）；不产生重复行 | I3 |
| 契约 | `GET /api/documents` 的响应字段与 `docs/api-contract.md` 一致（决策点 2 定了才写） | I5 |

## 10. 决策点（已拍板 2026-09-16）

用户确认：**全部按建议执行** —— 2=(b) 状态暴露给 API、5=(a)→(b) 先只报告后自动登记、
6=(a) 替换期间退出检索边界、8=(a) 本期一次性迁移命令；1=(a) StrEnum、3=(a) CLI 对账、
4=(a) 本期不做并发保护、7=(a) 保留 `DELETING`（P2 落地）。

### 10.1 P1 实现记录（与本文的差异）

- **多了一个 `status_reason` 列**（可空，200 字符）：§5.1 要求"记录原因"，只靠日志无法
  让对账/人工看到失败原因。它**不进 API 响应**（可能引用上游错误文本），与 403 的
  `permission_denied` 是同一种思路。
- **多了一个 `ConflictError`（409）**：§5.1 的"存在且 INDEXING → 409"需要一个语义准确的
  错误类型（`AlreadyExistsError` 表示"已存在"，而这里是"正在过渡中，稍后重试"）。
  同一个 409 也用于删除在途文档。
- **删除在途文档直接 409**（P1 的临时守卫）：不让删除与在途上传的晋升互相踩。P2 的
  `DELETING` 才是正解，届时这条守卫可以被替换掉。
- **`create_all()` 之外仍需一次性迁移**：已实现为 `openrag-lab migrate-registry`
  （幂等、按列判断、存量回填 `indexed`），并在本地 58 行真实数据上验证过。
  （P1 时叫 `migrate-registry-status`，P2 起推广为覆盖各期；旧名字保留为别名。）

### 10.1b P2 实现记录（逻辑删除 / 墓碑，与本文的差异）

- **删除不再删行**：改留 `DELETED` 墓碑（理由见 §5.2 的差异说明）。用户列表**不显示**墓碑
  （已经删掉的再列出来会读成"删除没生效"），但 `DELETING` 行仍显示 —— 那件事还在进行中。
  同名重传**复活**同一行（同 `id`、保留 `created_at`），所以墓碑不占新键、也不占 5000 配额
  （配额只数 `INDEXED`）。
- **多了一个 `remote_outcome_unknown` 列**（布尔，默认 FALSE）：记录"这一行的终态是**没有拿到
  远端定论**就定下的"（上传超时、删除超时）。它是 P3 的工作清单谓词
  （`WHERE remote_outcome_unknown`）。**不写进 `status_reason` 的散文里**：用解析措辞来驱动
  恢复，措辞一改就静默失效 —— `reingest.py` 早就为同一个理由改成按字段判定。
  本列与 `status` 一起由 `openrag-lab migrate-registry` 补齐（P1 那条命令的推广：一条命令
  覆盖各期，逐列判断，不再一期一个命令）。
- **端口契约要表达"没有定论"**：新增 `RagOutcomeUnknownError`（在 `domain/rag/ports.py`，
  属于端口契约而非域规则）。适配器负责区分三种结局：远端回 404 且形状为
  `success=false, deleted_chunks=0` → 归一化成"已无此物"的正常返回；传输层失败
  （`OpenRAGError.status_code is None`）→ 无定论；其余 → 原样抛出。**应用层不再解读
  OpenRAG 的状态码约定**，也不再用字符串匹配错误文案。
- **删除响应的契约变更**：多一个 `confirmed` 字段。超时仍返回 200（名字已离开检索边界），
  但 `confirmed=false` + `deleted_chunks=0` —— 报一句光秃秃的"已删除"会让猜测冒充事实。
- **P1 的临时守卫被正式语义取代**：`INDEXING` 时删除仍 409（这条守卫留着，见 §10.2），
  但 `DELETING` 时删除、以及 `DELETING` 时上传，也都由状态机自己拒掉 —— 三种 409
  在契约里各有明确含义（`docs/api-contract.md` §2.6/§2.7）。
- **删除作废 `openrag_document_id`**（`mark_deleting`）：id 声称"远端存在这份文档"，
  删除那一刻就不成立（评审第 1 轮的 issue ②）。必须在**删除时**清而不是重传时清 ——
  重传的回查是 best effort，查不到时 `mark_indexed` 不会覆盖，旧 id 就会长期留在行里，
  而它在 API 响应里可见。替换上传（非删除）仍然保留旧 id，理由是旧的仍然有效。
- **上传侧同样留"无定论"的痕**：入库超时会把 `FAILED` 行标为
  `remote_outcome_unknown=true`。这不是悲观，而是区分"确认没写"与"不知道写没写" ——
  只有这个区别能告诉对账要不要探活（这也是 P1 评审提的那条意见的正确修法：
  **不去猜存在性，而是把"不知道"如实记下来**）。

### 10.2 已知限制（P1 不做、P2/P4 再处理）

- 对账任务（P3）尚未实现：`INDEXING`/`FAILED`/`DELETING` 行、以及
  `remote_outcome_unknown=true` 的行，目前**可查询但不会自动收敛**。
  P2 只负责把它们如实记下来（"只留痕"）。
- 并发保护（决策 4）未做：请求与对账若同时改同一行，可能互相覆盖（单进程部署下风险低）。
- 删除与晋升的竞态：`INDEXING` 时删除仍 409，`DELETING` 时上传也 409，两条守卫合起来
  使"上传覆盖掉一次在途删除"没有可达的交错（领域方法各自拒掉非法边）。剩下的是
  "删除一个其实还在写远端的上传"这种跨进程时序，方向安全（检索不到，且列表可见），
  由 P3 收敛。
- 墓碑只增不减：没有保留期/清理策略（数量等于历史删除数）。等 P3 上线后按需加保留窗口。
- **被拒绝的删除无法通过 API 重试**：行停在 `DELETING` 后，再调删除接口是 409（"名字忙"）。
  这是刻意的（重试归 P3，见决策 11），但给 P3 留了一个必须解决的问题：
  **它的重试入口要能作用于 `DELETING` 行**（服务层的 `mark_deleting()` 会拒绝
  `DELETING`），否则对账拿不到能重试的路径。`status_reason` 已经在拒绝时写好，
  P3 可以直接用它区分"拒绝过"与"可能仍在途"。
- 阈值（P3 用）：`INDEXING` 的建议 stale 阈值是 `2 × UPLOAD_INGEST_TIMEOUT_SECONDS`
  （默认 600s；系数 2 覆盖"限流排队 + 一次完整入库等待"），`DELETING` 是
  `2 × client.timeout`（默认 120s）。**现在不写成配置项** —— 没有任何代码读它的配置
  等于另一种"配了但没生效"，等 P3 落地时再随代码一起加。

## 10.3 原决策表（含建议，供追溯）

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| 1 | 状态表示 | (a) `StrEnum` + 字符串列（照 `TenantStatus`/`UserStatus`） (b) 整数 | **(a)**：与既有约定一致，DB 里可读 |
| 2 | 是否把状态暴露给 API | (a) 列表只返回 `INDEXED`，契约零变更 (b) 返回全部 + 新增 `status` 字段（契约变更，要改 `docs/api-contract.md`） | **(b)**：否则"上传成功但列表里看不到"会让用户以为丢件；但这是契约变更，你定 |
| 3 | 对账触发方式 | (a) CLI 子命令（手动/cron） (b) lifespan 后台任务 (c) 两者 | **(a)**：显式、可控、不拖慢启动 |
| 4 | 并发保护（对账与请求同时改同一行） | (a) 不做（单进程部署下风险低） (b) 乐观锁 `version` 列 (c) 租约 `lease_until` | **(a) 现在**，把 (b) 记为后续；多 worker 化时再上 |
| 5 | 幽灵文档（远端有、本地无）处置 | (a) 只报告 (b) 自动登记 (c) 自动删除 | **(a) → (b)**：先只报告跑一段时间（避免误删），确认无副作用后再开自动登记 |
| 6 | 替换上传期间是否退出检索边界 | (a) 是（短暂搜不到） (b) 否（保持 `INDEXED`，贯穿替换） | **(a)**：宁可短暂搜不到，也不要搜到半新半旧 |
| 7 | 是否需要 `DELETING` 状态 | (a) 需要（对账能区分"删除卡住"） (b) 不需要（删除失败就靠重试 + 404 容忍） | **(a)**：状态数量少，但语义收益明确 |
| 8 | schema 迁移 | (a) 一次性迁移命令 (b) 现在引入 Alembic | **(a) 本期**，(b) 记为独立任务（它值得单独一个 PR） |
| 9 | 删除是逻辑删还是物理删 | (a) 逻辑删（墓碑） (b) 物理删行 | **(a)**（P2，2026-09-19）：删行会释放键、销毁"曾经登记过"的记录 —— 那正是远端残留无法被对账发现的根因 |
| 10 | 删除拿不到远端定论时怎么办 | (a) 算删除成功但记未确认 (b) 报失败留在 DELETING | **(a)**：名字已离开检索边界, 再报失败只会诱发无用的重试；但必须记下"未确认"，否则猜测冒充事实 |
| 11 | 删一个已是墓碑的名字 | (a) 404 (b) 顺手重试远端删除再返回成功 | **(a)**：重试是对账的职责, 挂回请求路径等于让死掉的请求成为唯一修复机会 |

## 11. 分期（每期独立可合并）

| 期 | 内容 | 依赖 | 规模 |
|---|---|---|---|
| **P1** | 状态列 + 三个状态（`INDEXING`/`INDEXED`/`FAILED`）+ 写路径改造 + **读路径只取 `INDEXED`** + 迁移命令 | 决策 1/6/8 | ~250 行 + 测试 |
| **P2** ✅ | `DELETING` 状态 + 删除路径改造 + **`DELETED` 墓碑**（决策 10/11） | P1、决策 7 | ~80 行（实际 ~300 行含测试与契约） |
| **P3** | 对账 CLI（report / --fix）+ 幽灵文档报告。**只留痕, 不写逻辑**（用户决策 2026-09-19）：先把
`INDEXING`/`FAILED`/`DELETING` + `remote_outcome_unknown` 的清单查询做出来 | P1、P2、决策 3/5 | ~200 行 + 测试 |
| **P4** | 并发保护 / 自动登记幽灵 / Alembic | 决策 4/5/8 | 独立评估 |

**P1 单独就有价值**：它消灭"幽灵文档不可见"（情形 A），且 `INDEXING` 行天然是待处理清单。
即使 P3 一直不做，P1 也让问题**从"日志考古"变成"一条 SQL 能查出来"**。

## 12. 明确不做（边界）

- ❌ 不做 2PC / XA / 分布式事务（物理上不可能，见 §1）
- ❌ 不改 OpenRAG 侧（不新增 owner 字段、不加回调）
- ❌ 不做"上传立刻返回任务 id + 轮询"的 API 形态改造（那是另一个改动，见 issue #12 讨论里
  提到的长请求问题；状态机是它的前置条件，不是替代）
- ❌ 不引入消息队列 / outbox 表（本地库单表 + 对账已够；outbox 是将来要跨服务扇出时才需要）
