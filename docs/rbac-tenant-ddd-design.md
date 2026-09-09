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
- 内置角色：`admin / developer / user / viewer`
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
  - addUser(user, role)
  - deactivate()

User（聚合根）
  - UserId
  - username
  - passwordHash
  - displayName
  - status
  - changePassword()

Role（聚合根）
  - RoleId
  - name
  - description
  - hasPermission(permission)
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
Tenant 1 ── * TenantUser * ── 1 User
Tenant 1 ── * TenantUserRole * ── 1 Role
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

1. 创建默认租户（如未指定，slug 由 username 生成）
2. 创建 User
3. 分配 user 角色
4. 返回 JWT
```

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
2. 加载该租户下用户角色
3. 汇总权限
4. 无权限 → 403
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

### 11.1 隔离策略

Phase 1 采用 **逻辑独立**：

```text
物理上共用一套 OpenRAG
每个租户拥有独立的 OpenRAG API Key / 服务账号
```

具体：

- 每个租户在 openrag-lab 中有唯一 `tenant_id`
- 每个租户对应一个独立的 OpenRAG API Key / 服务账号
- 使用该租户自己的 API Key 上传文档，OpenRAG 记录的 `owner` 天然归属该租户
- 调用 OpenRAG Search/Chat 时使用该租户自己的 API Key，并自动注入：

```json
{
  "filters": {
    "owners": ["<tenant-owner-id>"]
  }
}
```

`owner` 的取值由租户对应的 OpenRAG 服务账号决定，不依赖自定义 metadata。
自定义 metadata（如 `tenant_id`）仅用于展示/审计，不作为检索隔离条件。

### 11.2 为什么不用 data_sources 做租户隔离

`data_sources` 是文件级过滤，适合“某个知识库子集”；
租户隔离用 `owners` 更稳定，不依赖文件名。

### 11.3 未来升级

如果某个租户需要更强隔离：

```text
- 独立 OpenRAG API Key
- 独立 OpenSearch 索引
- 独立 OpenRAG 部署
```

这些可以做成 Infrastructure 层的部署策略，不影响 Domain 模型。

---

## 12. 数据模型

### tenants

```text
id
name
slug
status            # active / disabled
created_at
updated_at
```

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
name              # admin / developer / user / viewer
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
   创建 default 租户
   创建 admin / admin123
   分配 global_role: super_admin
   分配 tenant_role: tenant_admin
```

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
POST /api/search
POST /api/chat
GET  /api/documents
POST /api/documents/ingest
DELETE /api/documents/{filename}
```

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
│   │   ├── ports.py
│   │   └── models.py
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
│       ├── chat_service.py
│       └── search_service.py
├── infrastructure/
│   ├── db/
│   │   ├── models/          # users / tenants / roles / permissions / global_roles ...
│   │   ├── session.py
│   │   └── repositories/    # SQLAlchemy Repository 实现
│   ├── security/
│   │   ├── jwt.py
│   │   └── password.py
│   ├── openrag/
│   │   ├── client.py
│   │   └── openrag_port_impl.py
│   └── dify/
│       └── client.py
├── interfaces/
│   ├── api/
│   │   ├── main.py
│   │   ├── deps.py
│   │   └── routers/
│   │       ├── auth.py
│   │       ├── users.py
│   │       ├── tenants.py
│   │       ├── roles.py
│   │       ├── permissions.py
│   │       ├── search.py
│   │       ├── chat.py
│   │       └── documents.py
│   └── schemas/
└── cli.py
```

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

- SQLAlchemy async + SQLite
- 领域模型 + Repository
- JWT / Password
- 种子数据
- Auth / RBAC / User / Tenant API
- Search / Chat / Documents 接权限
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

- OpenRAG 文档入库时如何统一打 `tenant owner` 标签？
- 是否允许一个租户下多个角色叠加？
- 管理员能否跨租户管理？
- 后续是否需要“邀请码 / 邮箱验证”？
