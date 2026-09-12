# OpenRAG Lab：多租户 RBAC + DDD 设计文档

> 状态：草稿，待评审
> 目标读者：OpenRAG Lab 开发者 / AI 协作助手
> 对应分支：`feat/rbac-tenant-design`

---

## 1. 背景

OpenRAG Lab 正在从“CLI + 简单前后端”演进为自研产品应用层。

当前需要补上：

- 用户体系
- 认证
- 权限控制
- 租户隔离

OpenRAG 本身有 RBAC/用户/API Key 能力，但在我们的架构里它定位为 **RAG 引擎适配层**。
因此用户、角色、权限、租户应放在 openrag-lab 自己的应用层实现，而不是依赖 OpenRAG UI。

---

## 2. 目标

- 支持用户名 + 密码 + JWT 登录
- 支持用户注册 + 管理员创建用户两种途径
- 内置角色：`tenant_admin / developer / user / viewer`
- 支持 API 级 RBAC 拦截
- 支持租户级逻辑隔离
- 使用 DDD 分层，为后续扩展多平台、多工具、OAuth 等留下清晰边界
- 先写 API 契约，前端后续按契约对接

---

## 3. 非目标（Phase 1 不做）

- 审计日志（缓做）
- OAuth / SSO
- refresh token
- 前端管理页面
- 多租户切换（一个用户只属于一个租户）
- OpenRAG 物理级独立部署（先做逻辑隔离）
- 密码找回 / 邮箱验证

---

## 4. 总体架构

```text
┌────────────────────────────────────────────────────────────┐
│ React 前端（后续实现，按 API 契约对接）                      │
└──────────────────────────┬─────────────────────────────────┘
                           │ HTTP + JWT
┌──────────────────────────▼─────────────────────────────────┐
│ Interfaces（接口层）                                        │
│   FastAPI Router / Pydantic Schema / OpenAPI               │
├────────────────────────────────────────────────────────────┤
│ Application（应用层）                                       │
│   AuthService / UserService / TenantService / RbacService  │
├────────────────────────────────────────────────────────────┤
│ Domain（领域层）                                            │
│   Tenant / User / Role / Permission                        │
│   Repository 接口 / Domain Service                         │
├────────────────────────────────────────────────────────────┤
│ Infrastructure（基础设施层）                                │
│   SQLAlchemy Repository / JWT / Password / OpenRAG Client  │
└────────────────────────────────────────────────────────────┘
```

依赖方向：

```text
Domain 不依赖 Infrastructure
Application 依赖 Domain 接口 + Infrastructure 实现
Interface 只依赖 Application
```

ORM 策略：

- Domain 使用纯 Python 类 / dataclass
- SQLAlchemy 只存在于 Infrastructure Repository 中
- Domain 不 import SQLAlchemy / FastAPI / Pydantic

---

## 5. 限界上下文

初期划分两个上下文：

| Bounded Context | 职责 |
|---|---|
| Identity & Access | 用户、租户、角色、权限、认证 |
| RAG | OpenRAG 检索/对话/知识库适配 |

Identity & Access 是本阶段重点，RAG 上下文保持薄适配层。

---

## 6. 领域模型

### 6.1 聚合设计

```text
Tenant（聚合根）
  - TenantId
  - name
  - slug
  - status
  - document_namespace        # 只读派生：f"{slug}/"
  - scope_filename(filename)  # 生成带命名空间前缀的入库文件名
  - activate() / disable() / ensure_active()

User（聚合根）
  - UserId
  - tenant_id
  - username
  - password_hash
  - display_name
  - status
  - change_password_hash()
  - activate() / disable() / ensure_active()

Role（聚合根）
  - RoleId
  - name
  - description
  - is_system
  - permissions
  - has_permission(permission)

Document（实体，归属 Tenant）
  - DocumentId
  - tenant_id
  - stored_filename           # OpenRAG 中的实际文件名（含命名空间）
  - display_name              # 用户上传时的原始文件名
  - uploaded_by
  - mimetype / size_bytes
  - openrag_document_id
  - rename(display_name)      # stored_filename 不可变（含命名空间，是 OpenRAG 里的身份）
```

`Permission` 可作为值对象 / 只读实体：

```text
Permission
  - resource
  - action
  - name = f"{resource}:{action}"
```

### 6.2 核心关系

```text
Tenant 1 ── * User（User.tenant_id）
Tenant 1 ── * TenantUserRole * ── 1 User
TenantUserRole * ── 1 Role
Role 1 ── * RolePermission * ── 1 Permission

User 1 ── * UserGlobalRole * ── 1 GlobalRole
```

一个用户只属于一个租户（Phase 1）：

```text
User.tenant_id
```

Phase 1 内，一个用户在一个租户中只分配一个角色；
但 `tenant_user_roles` 表仍按多对多设计，后续放开限制不需要改表。

全局角色与租户角色分离：

```text
GlobalRole: super_admin
TenantRole: tenant_admin / developer / user / viewer
```

### 6.3 值对象

```text
TenantId
UserId
RoleId
Username
PasswordHash
PermissionName
TenantStatus
UserStatus
GlobalRoleName
TenantRoleName
```

### 6.4 领域不变量

- 用户名在系统内唯一
- 密码必须哈希后存储
- 租户 slug 唯一
- 同一租户内不能重复分配同一角色给同一用户
- super_admin 全局角色拥有跨租户管理权限
- tenant_admin 仅拥有当前租户管理权限
- 系统内置角色不可删除（Phase 1 可先不允许删除任何角色）

---

## 7. Repository 接口

放在 Domain 层：

```python
class TenantRepository(Protocol):
    async def find_by_id(self, tenant_id: TenantId) -> Tenant | None: ...
    async def find_by_slug(self, slug: str) -> Tenant | None: ...
    async def save(self, tenant: Tenant) -> None: ...

class UserRepository(Protocol):
    async def find_by_id(self, user_id: UserId) -> User | None: ...
    async def find_by_username(self, username: str) -> User | None: ...
    async def save(self, user: User) -> None: ...

class RoleRepository(Protocol):
    async def find_by_name(self, name: str) -> Role | None: ...
    async def list_roles(self) -> list[Role]: ...
    async def permissions_of(self, role_ids: list[RoleId]) -> set[Permission]: ...

class TenantUserRoleRepository(Protocol):
    async def roles_of_user_in_tenant(
        self, tenant_id: TenantId, user_id: UserId
    ) -> list[Role]: ...
    async def assign_role(
        self, tenant_id: TenantId, user_id: UserId, role_id: RoleId
    ) -> None: ...

class UserGlobalRoleRepository(Protocol):
    async def global_roles_of_user(self, user_id: UserId) -> list[GlobalRole]: ...
    async def assign_global_role(self, user_id: UserId, role_id: GlobalRoleId) -> None: ...

class DocumentRepository(Protocol):
    async def save(self, document: Document) -> None: ...
    async def find_by_id(self, document_id: DocumentId) -> Document | None: ...
    async def find_by_stored_filename(
        self, tenant_id: TenantId, stored_filename: str
    ) -> Document | None: ...
    async def list_by_tenant(self, tenant_id: TenantId) -> list[Document]: ...
    async def list_stored_filenames(self, tenant_id: TenantId) -> list[str]: ...
    async def delete(self, document_id: DocumentId) -> None: ...
```

---

## 8. Application Service

```text
AuthService
  - register(username, password, tenant_name?) -> TokenResult
  - login(username, password) -> TokenResult
  - me(actor) -> UserProfile

TenantService
  - create_tenant(name, slug) -> Tenant
  - get_tenant(tenant_id) -> Tenant

UserService
  - create_user_by_admin(admin_actor, username, password, tenant_id, role) -> User
  - list_users(actor, tenant_id) -> list[User]
  - activate/deactivate user

RbacService
  - has_permission(actor, permission) -> bool
  - assert_permission(actor, permission) -> None
  - list_user_permissions(actor) -> set[Permission]
```

---

## 9. 认证与授权流程

### 9.1 注册

```text
POST /api/auth/register
body: { username, password, display_name?, tenant_name? }

1. 创建该用户自己的新租户（tenant_name 只用于命名新租户；如未指定，slug 由 username 生成）
2. 如果同名/同 slug 租户已存在，则拒绝注册，防止加入已有租户并越权获得 tenant_admin
3. 创建 User
4. 分配 tenant_admin 角色
5. 返回 JWT
```

说明：

- 注册 = 自助开通自己的工作区
- 每个注册用户自动成为自己租户的 tenant_admin
- Phase 1 注册不能通过 tenant_name 加入已有租户
- 加入已有租户必须走管理员创建用户接口（需 `users:write` 权限）
- bootstrap 的 `default` 租户只用于本地超管引导，与用户自动创建的租户相互独立

### 9.2 登录

```text
POST /api/auth/login
body: { username, password }

1. 校验用户
2. 校验密码
3. 取用户所属租户
4. 返回 JWT
```

JWT payload：

```json
{
  "sub": "user-id",
  "tenant_id": "tenant-id",
  "username": "alice",
  "exp": 1710000000
}
```

### 9.3 管理员创建用户

```text
POST /api/users
Authorization: Bearer <admin token>
body: { username, password, tenant_id, role }

权限：users:write
```

### 9.4 权限判断

FastAPI 依赖：

```python
require_permission("search:use")
require_permission("users:write")
```

执行顺序：

```text
1. 从 JWT 解析 user_id + tenant_id
2. 加载 user_global_roles
   - 若包含 super_admin → 直接放行（跨租户全部权限）
3. 否则加载该租户下用户角色
4. 汇总租户权限
5. 无权限 → 403
```

---

## 10. 权限目录

Phase 1 建议：

```text
chat:use
search:use
documents:read
documents:upload
documents:delete
users:read
users:write
roles:read
roles:write
tenants:read
tenants:write
```

内置角色：

全局角色：

| 角色 | 权限 |
|---|---|
| super_admin | 跨租户全部权限 |

租户角色：

| 角色 | 权限 |
|---|---|
| tenant_admin | 本租户全部权限 |
| developer | documents:upload/read/delete、chat/search、users:read |
| user | chat/search、documents:read、documents:upload |
| viewer | chat/search、documents:read |

---

## 11. 租户隔离

### 11.1 隔离策略（Phase 1：文件名命名空间）

Phase 1 采用 **逻辑独立 + 文件名命名空间**：

```text
物理上共用一套 OpenRAG、一个 OpenSearch index
所有租户共用一把 OpenRAG API Key
租户边界用「文件名命名空间 + data_sources 过滤」表达，由 openrag-lab 强制
```

具体：

- 每个租户在 openrag-lab 中有唯一 `tenant_id` 和不可变 `slug`
- 租户文档命名空间固定为 `document_namespace = "<slug>/"`
- 入库时统一把文件名写成 `<slug>/<原始文件名>`（例如 `acme/报表.pdf`）
- openrag-lab 维护 `documents` 登记表，记录「已入库文件名 → 归属租户」
- 调用 OpenRAG Search/Chat 时，自动注入**本租户已登记的文件名**：

```json
{
  "filters": {
    "data_sources": ["acme/报表.pdf", "acme/制度.md"]
  }
}
```

租户边界不依赖 OpenRAG 的账号体系，也不需要自定义 metadata；
自定义 metadata（如 `tenant_id`）仅用于展示/审计，不作为隔离条件。

### 11.2 为什么不用 owners 做租户隔离（已实测否定）

最初的方案是「每租户一把 OpenRAG API Key，owner 天然归属该租户」，实测不成立：

```text
1. owner 只能在入库时由「认证身份」决定
   OpenRAG 公开 API 无法显式指定 owner（owner 由后端按请求身份注入）
2. 一把 API Key 只对应一个 OpenRAG 用户 → 只有一个 owner
   用同一把 key 给所有租户上传，所有文档 owner 完全相同，等于没有隔离
3. filters.owners 是对 owner 字段的精确（term）查询
   过滤一个「合成」的 owner（如 tenant:acme）只会得到空结果
```

实测结果：

```text
filters.owners = ["<共享账号 owner>"]     → 返回全部文档（无隔离）
filters.owners = ["tenant:<slug>"]        → 返回空（租户查不到任何东西）
filters.data_sources = ["<具体文件名>"]   → 精确命中
```

要做到真正的 owner 级隔离，前提是：

```text
每个租户一个独立的 OpenRAG 用户 + 该用户的 API Key
```

这属于 OpenRAG 的账号供应问题（需要管理员会话、用户管理），列入后续升级项。

### 11.3 为什么用 data_sources 作为租户边界

- `data_sources` 直接对应 filename 的精确匹配，可控、可测试
- 文件名列表来自我们自己的 `documents` 登记表，边界由应用层强制
- 不需要改造现有 OpenRAG 部署，Phase 1 即可验收

已知限制：

- `data_sources` 是精确匹配而非前缀匹配，所以**必须先有登记表**
- 未登记的文档不会被任何租户检索到（fail-closed，符合安全预期）
- 请求里的文件名列表随租户文档数增长，Phase 1 量级可接受；
  后续可改用 knowledge filter（`filter_id`）承载

### 11.3.1 s1p3b 必须遵守的 fail-closed 规则

实测（当前部署，2026-09-11）：

```text
filters = {"data_sources": []}   → 0 条结果（OpenRAG 会转成 __IMPOSSIBLE_VALUE__）
filters 缺省 或 filters = {}      → 未过滤，返回全部文档 ⚠️
```

所以检索链路必须：

```text
永远带上 data_sources 这个 key（租户没有文档时传空列表），否则退化为全库检索
```

这条要有专门的测试：租户无文档时检索必须返回空，而不是返回别人的文档。

实现落点（s1p3b）：

```text
application/rag/retrieval_scope.py
  RetrievalScope.filters  → 永远返回 {"data_sources": [...]}（空列表也带 key）
  RetrievalScopeResolver  → 决定目标租户并做越权判断
```

测试：`tests/rag/test_retrieval_endpoints.py::test_tenant_without_documents_still_sends_an_empty_scope`

### 11.3.2 命名空间前缀会被 OpenRAG 原样保留（实测）

带 `/` 的文件名不会被 OpenRAG 清洗或改写，端到端实测（2026-09-11）：

```text
上传 multipart filename = "smoke-tenant/smoke-report.md"
→ ingest 任务 completed
→ GET /api/v1/files/get_all 返回 filename = "smoke-tenant/smoke-report.md"（原样）
→ 该租户带 filters.data_sources 的检索命中，另一租户检索返回 0 条
→ DELETE /api/v1/documents {"filename": ...} 删除成功
```

这条是 D1 方案成立的前提：命名空间是文件名前缀，而不是自定义 metadata。

### 11.4 存量文档

Phase 1 之前入库的文档没有命名空间前缀，且 owner 属于共享账号。
处理方式（s1p3c 负责）：

```text
把这些文档按 `<slug>/<原文件名>` 重新入库，登记到对应租户
```

不在 Domain 里为「无前缀」开特例：命名空间规则保持唯一。

### 11.4.1 存量租户 slug

`slug` 是命名空间的来源，因此受一条不变量约束：

```text
非空、无首尾空白、内部无空白、不含 "/" 或 "\"、不是 "." / ".."
```

这条不变量在两个位置生效：

```text
写入路径：TenantService.create_tenant / Tenant.__post_init__  → 直接拒绝
读取路径：仓储把数据库行重建为 Tenant 时 → 抛可诊断的错误（带 tenant_id 与修复指引）
启动检查：lifespan 里扫一遍 tenants 表，把非法 slug 记进错误日志
```

为什么读取路径选择 fail-closed 而不是「降级放行」：
一个非法 slug 意味着该租户的命名空间不安全（`acme/eu` 会吞掉别的租户前缀），
放行等于让隔离失效。宁可让这一个租户不可用，也不让边界悄悄破掉。

数据现状（2026-09-11 核查）：本地库 tenants 表只有 `default` 一行，无违反不变量的记录；
能产生 slug 的路径只有 `slugify()`（非字母数字一律替换为 `-`）与 `create_tenant`（已校验），
因此现有代码产出的数据不会触发这条规则。

### 11.5 未来升级

```text
D1（当前）：共享 API Key + 文件名命名空间 + data_sources 过滤
D2：每租户独立 OpenRAG 用户 + API Key → 切换为 owners 维度
D3：每租户独立索引 / 独立 OpenRAG 部署
```

切换 D1 → D2 只影响 `infrastructure/openrag/tenant_scope.py` 这一个端口，
上层 Application / Domain 不需要改动。

---

## 12. 数据模型

### tenants

```text
id
name
slug              # 同时决定 document_namespace = "<slug>/"
status            # active / disabled
created_at
updated_at
```

> 说明：`document_namespace` 是 `slug` 的派生值（Domain 里是只读属性），不单独存列；
> slug 不可变，因此命名空间不需要重写。

### documents

```text
id
tenant_id             # 归属租户
stored_filename       # OpenRAG 中实际的文件名（含命名空间前缀）
display_name          # 用户上传时的原始文件名
uploaded_by           # 上传者 user_id
mimetype
size_bytes
openrag_document_id   # OpenRAG 返回的 document_id（用于删除/对账）
created_at
updated_at

unique(tenant_id, stored_filename)
```

`documents` 是租户隔离的落点：检索时用 `list_stored_filenames(tenant_id)` 生成
`filters["data_sources"]`。

### users

```text
id
tenant_id
username
password_hash
display_name
status            # active / disabled
created_at
updated_at
```

### roles

```text
id
name              # tenant_admin / developer / user / viewer
description
is_system
```

### permissions

```text
id
resource
action
name              # chat:use
```

### role_permissions

```text
role_id
permission_id
```

### tenant_user_roles

```text
tenant_id
user_id
role_id
```

### global_roles

```text
id
name              # super_admin
description
is_system
```

### user_global_roles

```text
user_id
global_role_id
```

---

## 13. 启动种子数据

应用启动时自动：

```text
1. 创建权限目录
2. 创建全局角色 super_admin
3. 创建租户角色 tenant_admin / developer / user / viewer
4. 如果没有任何用户：
   创建 default 租户（仅本地引导用）
   创建 admin / admin123
   分配 global_role: super_admin
   分配 tenant_role: tenant_admin
```

安全说明：

- `admin/admin123` 仅用于本地开发环境引导
- 生产环境管理员口令必须从环境变量读取，不写入种子数据
- 后续增加“首次登录强制改密”

---

## 14. API 契约（Phase 1）

### 公开接口

```text
POST /api/auth/register
POST /api/auth/login
GET  /api/health
```

### 登录后

```text
GET  /api/auth/me
POST /api/search      # 需要 search:use（s1p3b）
POST /api/chat        # 需要 chat:use（s1p3b）
GET  /api/documents   # s1p3c（旧的无鉴权版本已在 s1p3b 移除）
POST /api/documents/ingest      # s1p3c
DELETE /api/documents/{filename} # s1p3c
```

> s1p3b 移除了旧的无鉴权 `GET /api/documents`：它直接返回全库文件名清单，
> 在其它接口都收了权限之后继续留着就是一个现成的泄露面。
> s1p3c 会以「需要 `documents:read` + 只返回本租户已登记文档」的契约重新提供。

#### POST /api/search（s1p3b 已实现）

```json
{
  "query": "投诉时限",
  "limit": 10,
  "score_threshold": 0,
  "rerank": false,
  "rerank_model": null,
  "rerank_top_n": null,
  "tenant_id": null
}
```

```json
{
  "results": [{"filename": "acme/制度.md", "text": "...", "score": 0.82}],
  "scope": {"tenant_id": "...", "document_count": 1, "cross_tenant": false}
}
```

规则：

- **不接受客户端 `filters`**：租户边界由服务端按 `documents` 登记表生成；
  传未知字段（含 `filters`）直接 422，避免调用方误以为自己的过滤生效了
- `tenant_id` 只有 `super_admin` 能用；其他角色传了就 403
- 不传 `tenant_id` 时一律限定调用者自己的租户（`super_admin` 也一样）
- 响应中的 `scope` 显式回显本次边界，便于前端与审计确认

#### POST /api/chat（s1p3b 已实现）

请求同构（`message` + `limit` + `score_threshold` + 可选 `tenant_id`），响应：

```json
{"response": "...", "scope": {"tenant_id": "...", "document_count": 1, "cross_tenant": false}}
```

状态码：401 未登录 / 403 无权限或跨租户 / 404 租户不存在 / 502 OpenRAG 调用失败。

### 管理接口（权限保护）

```text
GET    /api/users
POST   /api/users
PATCH  /api/users/{id}
DELETE /api/users/{id}

GET    /api/roles
POST   /api/roles
PATCH  /api/roles/{id}
DELETE /api/roles/{id}

GET    /api/permissions
GET    /api/tenants
POST   /api/tenants
```

### 统一错误

```json
{
  "detail": "permission_denied"
}
```

---

## 15. DDD 目录结构

```text
src/openrag_lab/
├── domain/
│   ├── identity/
│   │   ├── models.py
│   │   ├── repository.py
│   │   └── services.py
│   ├── rag/
│   │   └── ports.py               # RagGateway 出站端口（Phase 1 无独立领域模型）
│   └── shared/
│       ├── errors.py
│       ├── ids.py
│       ├── enums.py
│       ├── events.py
│       └── constants.py
├── application/
│   ├── identity/
│   │   ├── auth_service.py
│   │   ├── user_service.py
│   │   ├── tenant_service.py
│   │   └── rbac_service.py
│   └── rag/
│       ├── retrieval_scope.py     # 租户边界解析 + 越权判断（fail-closed）
│       ├── search_service.py
│       └── chat_service.py
├── infrastructure/
│   ├── db/
│   │   ├── models/          # users / tenants / documents / roles / permissions ...
│   │   ├── session.py
│   │   ├── integrity.py     # 启动期数据自检
│   │   └── repositories/    # SQLAlchemy Repository 实现
│   ├── security/
│   │   ├── jwt.py
│   │   └── password.py
│   ├── openrag/
│   │   ├── client.py              # TODO: 仍留在仓库顶层的 client.py，后续搬进来
│   │   ├── tenant_scope.py        # 租户 → OpenRAG 调用参数（key + 命名空间）
│   │   └── openrag_port_impl.py   # RagGateway 的 OpenRAG 实现
│   └── dify/
│       └── client.py
├── interfaces/
│   ├── api/
│   │   ├── main.py                # TODO: 仍在 api/main.py
│   │   ├── deps.py
│   │   ├── errors.py              # 领域错误 → HTTP 状态码（app 与测试共用）
│   │   └── routers/
│   │       ├── auth.py
│   │       ├── users.py
│   │       ├── tenants.py
│   │       ├── roles.py
│   │       ├── search.py          # s1p3b：已迁入（原 api/routers/search.py 删除）
│   │       ├── chat.py            # s1p3b：已迁入（原 api/routers/chat.py 删除）
│   │       └── documents.py       # s1p3c（原无鉴权的 api/routers/documents.py 已删除）
│   └── schemas/
└── cli.py
```

已知偏差（后续阶段收敛）：

```text
- 顶层 client.py 与 api/main.py 仍在旧位置（api/routers/ 现只剩 health.py）
- s1p3c 会把 documents 路由写进 interfaces/api/routers/ 并接上登记表
```

**前端影响**：`/api/search`、`/api/chat`、`/api/documents` 现在都要求登录态，
现有 lab 前端（`frontend/src/lib/api.ts`）不带 Authorization 头，会收到 401。
接线属于 Phase 2 前端工作。

---

## 16. 贫血/充血策略

- Phase 1 以贫血 + Domain Service 为主
- 关键聚合（Tenant / User / Role）从第一天就写关键不变量方法
- 后续随业务复杂度提高，逐步把更多逻辑收进实体

示例：

```python
class Tenant:
    def add_user(self, user_id: UserId, role: Role) -> TenantUserRole:
        # 不变量：不能重复添加同一角色
        ...
```

---

## 17. 开发阶段

### Phase 1：领域 + 基础设施 + API

```text
s1p0  设计文档与契约                      ✅
s1p1  DDD 骨架 + 数据模型 + Repository    ✅
s1p2  Auth / User / Tenant / RBAC API     ✅
s1p3a 租户文档作用域（命名空间 + 登记表）  ✅
s1p3b Search / Chat 接 RBAC + 租户过滤     ✅
s1p3c Documents 接 RBAC + 命名空间 + 登记表
```

- SQLAlchemy async + SQLite
- 领域模型 + Repository
- JWT / Password
- 种子数据
- Auth / RBAC / User / Tenant API
- Search / Chat / Documents 接权限与租户隔离
- OpenAPI 契约

### Phase 2：前端

- Login / Register
- Profile
- 管理员用户管理页
- 角色管理页（可选）

### Phase 3：增强

- Audit Log
- Refresh Token
- OAuth
- 多租户切换
- OpenRAG 独立部署策略

---

## 18. 待确认/开放问题

- 存量文档如何迁到命名空间前缀（倾向重新入库，见 11.4）
- 单次检索注入的 `data_sources` 列表上限与分批策略
- 是否允许一个租户下多个角色叠加？
- 每租户独立 OpenRAG 用户 / API Key 的供应方式（D2 升级项）
- 后续是否需要“邀请码 / 邮箱验证”？
