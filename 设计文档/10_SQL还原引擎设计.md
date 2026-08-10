# SQL 还原引擎设计

> 从字段 DAG 反向生成 SQL 语句 —— 声明式节点规约 + 递归自包含

## 1. 架构分层

```
┌─ field_to_sql(hash)        穿透版递归（subquery_to_sql 积木块）
├─ field_to_sql_full(hash,sq) 声明式完整还原（前端人类可读）
│   ├─ _LEAF_SPEC  声明式规约表（所有节点类型）
│   │   └─ base(fh, e, exprs, cur) → (sql_str, exposed_dict)
│   └─ 递归自包含：每个 base 调用 _compute_base(child)
└─ subquery_to_sql(sq_hash)   编排层（内部调用 field_to_sql）
```

## 2. 单一规约表：_LEAF_SPEC

所有节点统一处理——递归子节点获取参数，注入自身信息。

| 类别 | 节点 | base 函数 | 递归逻辑 |
|------|------|----------|---------|
| 叶子 | `column` | `_col_base` | 无子节点，返回所属表 |
| 叶子 | `literal` | `_literal_base` | 无子节点 |
| 叶子 | `star` | `_star_base` | 无子节点，返回所属表 |
| 叶子 | `current_timestamp` | `_ts_base` | 无子节点 |
| 条件 | `eq / gt / and / ...` | `_cond_base` | 用 `_leaf_tables` 收集子节点真实表名 |
| 条件 | `in / notin / div / mul / ...` | `_cond_base` | 同上 |
| 聚合 | `count / sum / avg / min / max` | `_agg_base` | 递归 `args[0]`，保留子节点全部信息后包裹 `COUNT(...)` |
| 结构 | `join` | `_join_base` | 递归 `args[0]` → 列，加上 ON 条件表信息 |
| 结构 | `where` | `_where_base` | 递归 `args[0]`，注入 WHERE 条件 |
| 结构 | `groupby` | `_groupby_base` | **双路径递归**：`args[0]` + `group_keys` |
| 结构 | `having` | `_having_base` | 递归 `args[0]`，注入 HAVING 条件 |
| 结构 | `limt` | `_limt_base` | 递归 `args[0]`，注入 LIMIT + ORDER BY |

**无 wrap 链**：每个 base 已递归包含所有下层信息，无需向上追溯 `field_references`。

## 3. 参数传递模型：exposed dict

`exposed` 是递归过程中逐层累积的参数集，结构节点在子节点的基础上加料：

```
exposed = {
    col:      "SQL片段",           # 节点自身的 SQL 表达式
    tables:   {table1, table2},    # 涉及的表名集合
    on:       {"cond1", "cond2"},  # JOIN ON 条件集合
    where:    {"cond"},            # WHERE 条件集合
    groupby:  {"col"},             # GROUP BY 键集合
    having:   {"cond"},            # HAVING 条件集合
    orderby:  {"col ASC"},         # ORDER BY 集合
    limit:    10,                  # LIMIT 值
}
```

## 4. 递归示例

以 `dim_mchnt.mchnt_nm` 在 `FROM A LEFT JOIN B` 中的路径为例：

```
_col_base(column)
  → {col: "dim_mchnt.mchnt_nm", tables: {dim_mchnt}}

_join_base(join,  children=[column])
  → 递归 _compute_base(column) → 获得 {col, tables: {dim_mchnt}}
  → 加上 on 条件 → on_tables = {trans_flow}
  → {col: "dim_mchnt.mchnt_nm", tables: {dim_mchnt, trans_flow},
     on: {"dim_mchnt.mchnt_cd = trans_flow.mchnt_cd"}}
```

以 `COUNT(*)` 在 `WHERE + GROUP BY` 中的路径为例：

```
_star_base(star)
  → {col: "trans_flow.*", tables: {trans_flow}}

_where_base(where, children=[star])
  → 递归 _compute_base(star) → {col, tables: {trans_flow}}
  → 加上 WHERE → {col, tables: {trans_flow}, where: {"trans_status = 'SUCCESS'"}}

_agg_base(count, children=[where])
  → 递归 _compute_base(where) → {col, tables, where}
  → 包裹 COUNT → {col: "COUNT(trans_flow.*)", tables: {trans_flow},
                  where: {...}}

_groupby_base(groupby, children=[count], group_keys=[dim_domin.region_nm])
  → 路径1: 递归 _compute_base(count) → {col, tables: {trans_flow}, where: {...}}
  → 路径2: 递归 _compute_base(group_key_column) → {tables: {dim_domin, trans_flow},
             on: {"region_cd = region_cd"}}
  → 合并: {col: "COUNT(trans_flow.*)",
           tables: {trans_flow, dim_domin},
           on: {...}, where: {...},
           groupby: {"dim_domin.region_nm"}}
```

## 5. groupby 双路径合并

这是唯一采用多路递归的节点。GROUP BY 语义上是查询级操作，包含两类信息：

| 路径 | 来源 | 提供什么 |
|------|------|---------|
| `args[0]` | 被包裹的投影列 (count/sum 等) | col, tables, where, having |
| `group_keys` | GROUP BY 键列 | 额外表、额外 JOIN ON 条件 |

两条路径的 `tables`、`on`、`where` 取并集，确保 SELECT 中同时包含聚合表达式和 GROUP BY 键。

## 6. 组装规则

```
if 节点类型 ∈ 条件集合 (eq/gt/and/div/...):
    return col                   ← 纯表达式，直接输出

SELECT: groupby_keys + col       ← groupby 键在前，聚合表达式在后（去重）
FROM:   col 所属表（优先）或 tables[0]
JOIN:   tables[1:] 匹配 ons[i]
WHERE:  wheres 用 AND 连接
GROUP BY: groupbys
HAVING:  havings
ORDER BY: orderbys
LIMIT:   limit
```

条件和表信息分离：`_cond_base` 返回真实表名（供父节点合并），组装层按节点类型决定是否包 SELECT。

## 7. 关键设计决策

| 决策 | 为什么 |
|------|--------|
| 无 wrap 链，纯递归 | 每个 base 已自包含——`_where_base`递归`_join_base`→`_col_base`，无需向上追溯 |
| 条件节点 tables=∅ | eq/gt 等是纯表达式，不应包 SELECT 外套 |
| 聚合递归 _compute_base 而非 _f | `_f` 是穿透版，会丢失 WHERE 等结构信息 |
| groupby 双路径 | GROUP BY 涉及两类信息源，单路径递归信息不全 |
| 运行时 sq_fields 范围限制 | 共享字段跨测例复用，限制在当前子查询内 |
| `_f` 穿透版不变 | `subquery_to_sql` 走独立编排，不受声明式影响 |

## 8. L2-04 还原实例

```sql
-- 原始 SQL
SELECT a.trans_id, b.mchnt_nm
FROM trans_flow a LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
```

```
a0636f2c (column):  select trans_flow.trans_id from trans_flow
a94bfb06 (column):  select dim_mchnt.mchnt_nm from dim_mchnt
e806141d (eq):      dim_mchnt.mchnt_cd = trans_flow.mchnt_cd
b1590719 (join):    select dim_mchnt.mchnt_nm from dim_mchnt
                      join trans_flow on dim_mchnt.mchnt_cd = trans_flow.mchnt_cd
```

## 9. L2-05 还原实例

```sql
-- 原始 SQL
SELECT b.region_nm, count(1) AS trans_cnt
FROM trans_flow a LEFT JOIN dim_domin b ON a.region_cd = b.region_cd
WHERE a.trans_status = 'SUCCESS'
GROUP BY b.region_nm
```

```
join:    select dim_domin.region_nm from dim_domin
           join trans_flow on region_cd = region_cd

where:   select dim_domin.region_nm from dim_domin
           join trans_flow on region_cd = region_cd
         where trans_status = 'SUCCESS' AND ...

groupby: select dim_domin.region_nm, COUNT(trans_flow.*)
         from dim_domin join trans_flow on region_cd = region_cd
         where trans_status = 'SUCCESS' AND ...
         group by dim_domin.region_nm
```

## 10. 加新节点类型

```python
_LEAF_SPEC["new_op"] = lambda fh, e, exprs, cur: (
    "SQL_片段",
    {"col": "...", "tables": {...}, ...}
)
```

不改任何核心循环逻辑。

## 11. 与持久化层的关系

```
fields.expression (JSONB)
    ↓ 内容寻址 SHA256[:16] — 不可变

运行时参数传递 (exposed dict)
    ↓ 递归 _compute_base — 纯函数，不持久化

节点类型规约 (_LEAF_SPEC)
    ↓ Python 代码 — 声明式注册
```
