# SQLineage REST API 设计

> 版本: v2.0 | 框架: Python (FastAPI) | 前缀: `/api/v1`  
> 变更: 新增版本历史 API | 新增血缘升级 API | hash 改为 16 位

---

## 1. 通用约定

### 1.1 响应格式

```json
// 成功
{ "code": 0, "data": {...}, "message": "ok" }

// 失败
{ "code": 40001, "data": null, "message": "具体错误描述" }
```

### 1.2 认证

除 `/api/v1/auth/login` 外，所有接口需携带 Header:
```
Authorization: Bearer <session_token>
```

### 1.3 分页

请求参数: `page`(默认1), `page_size`(默认20)
响应:
```json
{ "code": 0, "data": { "items": [...], "total": 150, "page": 1, "page_size": 20 } }
```

---

## 2. 认证模块 `/api/v1/auth`

### 2.1 `POST /api/v1/auth/login`

**Request:** `{ "username": "admin", "password": "nbkknb" }`  
**Response:** `{ "code": 0, "data": { "token": "...", "user": { "id": 1, "username": "admin", "role": "admin" }, "expires_at": "..." } }`  
**错误码:** 40101 — 用户名或密码错误

### 2.2 `POST /api/v1/auth/logout`

无 Body。响应 `{ "code": 0 }`

### 2.3 `GET /api/v1/auth/me`

响应当前用户信息。

---

## 3. 用户管理模块 `/api/v1/users` (仅 admin)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/users` | 用户列表（分页） |
| POST | `/users` | 创建用户 `{ username, password, role }` |
| PUT | `/users/{id}` | 修改角色或密码（字段可选） |
| DELETE | `/users/{id}` | 删除用户 |

---

## 4. 表结构模块 `/api/v1/tables`

### 4.1 `POST /api/v1/tables` — 从建表语句构建表结构

**Request:**
```json
{
  "ddl": "CREATE TABLE bs_comdb.trans_flow (\n  trans_id VARCHAR(32) COMMENT '交易ID',\n  mchnt_cd VARCHAR(16) COMMENT '商户号'\n) COMMENT '交易流水表';",
  "source": "root"
}
```
- `source=root`: 自动构建 atomic 字段节点，写入 `table_column_mappings`（status=active），回填 `table_columns.field_hash` 冗余指针。
- `source=middle`: 仅保存表结构，不生成字段节点。

**Response:** 表信息 + 列清单（含 `ordinal_position`, `field_hash`）  
**错误码:** 41001 表已存在 / 41002 DDL 解析失败

### 4.2 `GET /api/v1/tables` — 表列表

**Query:** `?source=root&keyword=trans&page=1&page_size=20`

### 4.3 `GET /api/v1/tables/{table_id}` — 表详情

含列信息、当前 active 的 `field_hash` 映射、当前 active 的 `input_subquery_hash`。

### 4.4 `PUT /api/v1/tables/{table_id}` — 更新表结构

DDL 变更做 diff 比对：新增列追加，删除列标记。

### 4.5 `DELETE /api/v1/tables/{table_id}` — 删除表

校验无活跃引用后硬删除。

### 4.6 `GET /api/v1/tables/{table_id}/input-history` — **新增** 输入子查询历史

**Response:**
```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": 3,
        "subquery_hash": "a1b2c3d4",
        "status": "active",
        "deprecated_at": null,
        "deprecated_reason": null,
        "created_at": "2026-06-20T10:00:00Z"
      },
      {
        "id": 2,
        "subquery_hash": "e5f6a7b8",
        "status": "deprecated",
        "deprecated_at": "2026-06-20T10:00:00Z",
        "deprecated_reason": "root→middle 升级，血缘链延长",
        "created_at": "2026-06-19T08:00:00Z"
      }
    ]
  }
}
```

### 4.7 `GET /api/v1/tables/{table_id}/columns/{col_id}/mapping-history` — **新增** 字段映射历史

**Response:** 该列所有 `table_column_mappings` 记录（active + deprecated 列表）。

---

## 5. 血缘构建模块 `/api/v1/lineage`

### 5.1 `POST /api/v1/lineage/build` — 核心：输入 SQL，构建血缘

**Request:**
```json
{
  "sql": "SELECT a.mchnt_cd, b.mchnt_nm FROM trans_flow a LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd",
  "sink_table": "bs_comdb.result_table"
}
```
`sink_table` 可选：不传则只构建 DAG，不绑定输出表。

**校验流程（按顺序，遇错即报）:**
1. SQL 语法解析 (sqlglot)
2. LIMIT 禁用检查 → `49001`
3. JOIN 规范检查 → `49002`
4. 引用的表是否均已构建表结构 → `49003`
5. middle 表是否具备血缘 → `49004`
6. root 表字段 hash 一致性校验 → `49005`
7. (有 sink_table 时) 投影列数与表结构列数是否一致 → `49006`

**Response:**
```json
{
  "code": 0,
  "data": {
    "new_fields": [{ "hash": "a1b2", "field_type": "expression", "expression": {...} }],
    "new_subqueries": [{ "hash": "s1q2", "type": "select", "structure": {...} }],
    "total_new_nodes": 15,
    "reused_nodes": 42,
    "sink_binding": {
      "input_subquery": "s1q2",
      "column_mappings": [
        { "ordinal_position": 1, "column_name": "trans_id", "field_hash": "f1a2" },
        { "ordinal_position": 2, "column_name": "mchnt_cd", "field_hash": "f3b4" }
      ]
    }
  }
}
```

**错误码:**

| code | 说明 |
|---|---|
| 49001 | SQL 中包含 LIMIT，禁止入血缘 |
| 49002 | JOIN 写法不规范 |
| 49003 | 引用的表 `{schema.table}` 未构建表结构 |
| 49004 | middle 表 `{schema.table}` 缺少血缘数据 |
| 49005 | root 表 `{schema.table}.{column}` hash 不匹配 |
| 49006 | 子查询投影列数 ({n}) 与表结构列数 ({m}) 不一致 |

### 5.2 `POST /api/v1/lineage/trace` — 血缘追溯 / 影响分析

**Request:**
```json
{ "field_hash": "a1b2", "direction": "upstream", "depth": 10 }
```
`direction`: `upstream` (追溯) | `downstream` (影响分析)

**Response:** 包含 nodes + edges 的 DAG 子图。

---

### 5.3 `POST /api/v1/lineage/upgrade` — **新增** root→middle 升级

> 当 root 表的祖先链路被补充后，触发全链路级联重建。

**Request:**
```json
{
  "table_id": 5,
  "ancestor_tables": [
    { "ddl": "CREATE TABLE ...", "source": "root" },
    { "ddl": "CREATE TABLE ...", "source": "root" }
  ],
  "extended_sql": "INSERT INTO upgraded_table SELECT ... FROM ancestor_a JOIN ancestor_b ..."
}
```
- `ancestor_tables`: 祖先表的建表语句（逐个构建）
- `extended_sql`: 连接祖先表到目标表的血缘 SQL

**行为（单事务）:**
1. 构建祖先表结构
2. 从 `field_references` 提取目标表的完整下游引用树
3. 执行 `extended_sql` 血缘构建（此时目标表已是 middle）
4. 沿引用树级联刷新所有字段 hash
5. 软废弃相关追踪记录（`table_input_subqueries`、`table_column_mappings`、`natural_semantics`）
6. 自然语义从旧 hash 复用至新 hash

**Response:**
```json
{
  "code": 0,
  "data": {
    "upgraded_table": "bs_comdb.result_table",
    "cascaded_fields": 245,
    "new_fields": 120,
    "deprecated_mappings": 18,
    "deprecated_semantics": 245,
    "old_to_new_map": { "a1b2": "x1y2", ... }
  }
}
```

---

## 6. 节点查询模块 `/api/v1/nodes`

### 6.1 `POST /api/v1/nodes/search` — 模糊搜索

**Request:**
```json
{
  "keyword": "mchnt_cd",
  "node_type": "field",
  "search_in": ["hash", "expression", "semantics"],
  "page": 1,
  "page_size": 20
}
```

**Response:** 分页结果，每项含 `hash`, `field_type`, `expression_summary`, `semantics`, `related_tables`

### 6.2 `GET /api/v1/nodes/{hash}` — 节点详情

含节点信息 + 上游引用链 + 下游依赖链各 1 层。

---

## 7. 自然语义模块 `/api/v1/semantics`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/semantics/{hash}?citizen_type=field` | 当前活跃语义 |
| PUT | `/semantics/{hash}` | 手动修改 (expert+)。旧 semantic → deprecated，新 → active |
| POST | `/semantics/ai-infer` | AI 推断（不写入） |
| POST | `/semantics/ai-confirm` | 确认 AI 推断并写入 |
| GET | `/semantics/{hash}/history?citizen_type=field` | 语义变更历史（含 `deprecated_reason`） |

---

## 8. 审计/管理模块 `/api/v1/admin`

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/admin/validate-references` | 手动触发 field_references 一致性校验 |
| GET | `/admin/stats` | 系统概览统计 |

---

## 9. 错误码汇总

| 范围 | 模块 |
|---|---|
| 401xx | 认证 |
| 403xx | 权限 |
| 404xx | 资源不存在 |
| 410xx | 表结构 |
| 420xx / 490xx | 血缘构建 |
| 430xx | 节点查询 |
| 440xx | 自然语义 |

---

## 10. 权限矩阵

| 接口 | guest | user | expert | admin |
|---|---|---|---|---|
| 认证 | ✓ | ✓ | ✓ | ✓ |
| 查看 (表/血缘/节点/语义) | ✓ | ✓ | ✓ | ✓ |
| 构建表结构 | ✗ | ✓ | ✓ | ✓ |
| 构建血缘 | ✗ | ✓ | ✓ | ✓ |
| root→middle 升级 | ✗ | ✗ | ✗ | ✓ |
| AI 推断 / 手动编辑语义 | ✗ | ✗ | ✓ | ✓ |
| 管理用户 | ✗ | ✗ | ✗ | ✓ |
| 一致性校验 | ✗ | ✗ | ✗ | ✓ |
