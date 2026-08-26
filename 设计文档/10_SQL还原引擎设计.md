# SQL 还原引擎设计

> 从字段 DAG 反向生成 SQL 语句 —— 节点分类规约 + 递归自包含

## 1. 架构分层

```
┌─ field_to_sql(hash)         穿透版递归（subquery_to_sql 积木块）
├─ field_to_sql_full(hash,sq) 完整还原（前端人类可读）
│   ├─ _SPEC  节点分类规约表（op → shape/param/prop）
│   └─ 两个分发器：_f 读 shape，_compute_base 读 prop
└─ subquery_to_sql(sq_hash)   编排层（内部调用 _f）
```

## 2. 单一规约表：_SPEC

所有节点 op 统一映射到 `(shape, param, prop)` 三元组，两个分发器各取所需，**不再各自穷举 op**。

| 三元组位 | 消费方 | 含义 |
|---------|--------|------|
| `shape` | `_f` | 渲染形状：怎么把子片段拼成自己的 SQL 片段 |
| `param` | `_f` | shape 的参数：atomic/special 传渲染函数，cmp/arith 传符号，keyword 传关键字，其余 None |
| `prop` | `_compute_base` | 结构传播：这个节点对 `exposed` 做什么 |

**渲染形状（shape）**：

| shape | 说明 | 成员 |
|-------|------|------|
| `atomic` | 无子节点，直接字符串 | column / literal / star / current_timestamp |
| `penetrate` | 穿透 args[0] | alias / join / where / having / groupby / limt |
| `cmp` | `a OP b`，字面量右移 | eq / neq / gt / lt / gte / lte / like / ilike / is / isdistinctfrom / similarto |
| `arith` | `(a OP b)`，带括号 | div / mul / add / sub |
| `keyword` | `a KW b KW c` | and / or / xor |
| `unary` | `KW a`（单目） | not |
| `prefix` | `FUNC(a, b, …)`，名称 = op.upper() | count/sum/avg/min/max + **所有未知标量函数** |
| `special` | 各自定制渲染函数 | in / notin / cast / round / if / casewhen / window / ordr / paren |

**结构传播（prop）**：

| prop | 行为 | 成员 |
|------|------|------|
| `leaf` | 只贡献表（column/star 有表，其余无） | column / literal / star / current_timestamp |
| `inherit` | 透明继承子节点 exposed（alias 的 AS 名由 field_to_sql_full 顶层体现） | alias |
| `merge` | 合并所有子节点的结构上下文（tables/where/joins/having/groupby） | 所有条件/算术/标量/special |
| `aggregate` | 继承子结构 + 包 `FUNC()` | count / sum / avg / min / max |
| `structural` | 各自加结构键（走 `_STRUCTURAL`） | join / where / having / groupby / limt |

**关键决策：prefix 兜底只面向「开集」标量函数**。标量函数的唯一变化量是函数名（即 op 本身），渲染统一为 `OP(args)`。因此 `_DEFAULT_SPEC = ("prefix", None, "merge")`，`date_format`、`abs`、`ceil`、`length` 等**未来任何新函数**都无需再写一行分支。而**运算符是「闭集」**（eq/neq/gt/like/is/and/or/xor/not/…），必须显式注册进 cmp/arith/keyword/unary，否则会错误落入 prefix 兜底渲染成 `LIKE(a,b)` 这种非法函数调用。

## 3. 参数传递模型：exposed dict

`exposed` 是递归过程中逐层累积的参数集，结构节点在子节点的基础上加料：

```
exposed = {
    col:      "SQL片段",            # 节点自身的 SQL 表达式
    tables:   {table1, table2},     # 涉及的表名集合
    joins:    [(table, on_sql), …], # JOIN 序列（有序，ON 与右表一一绑定）
    where:    {"cond"},             # WHERE 条件集合
    groupby:  {"col"},              # GROUP BY 键集合
    having:   {"cond"},             # HAVING 条件集合
    orderby:  {"col ASC"},          # ORDER BY 集合
    limit:    10,                   # LIMIT 值
}
```

`joins` 是**有序 list**（不是无序 set）：`_join_base` 每层推导「本层 JOIN 引入的右表 = ON 条件涉及的表 − 子节点已累积的表」，把 `(右表, on_sql)` 追加到 child 的 joins 末尾，从而**保持 JOIN 顺序 + ON 与表的一一对应**（修复多表连续 JOIN 乱序问题）。

## 4. 递归示例

以 `dim_mchnt.mchnt_nm` 在 `FROM A LEFT JOIN B` 中的路径为例：

```
_leaf_column(column)
  → {col: "dim_mchnt.mchnt_nm", tables: {dim_mchnt}}

_join_base(join, children=[column])
  → 递归 _compute_base(column) → {col, tables: {dim_mchnt}}
  → 本层右表 = {dim_mchnt, trans_flow} - {dim_mchnt} = {trans_flow}
  → {col: "dim_mchnt.mchnt_nm", tables: {dim_mchnt, trans_flow},
     joins: [("trans_flow", "dim_mchnt.mchnt_cd = trans_flow.mchnt_cd")]}
```

以 `COUNT(*)` 在 `WHERE + GROUP BY` 中的路径为例：

```
_leaf_star(star)
  → {col: "trans_flow.*", tables: {trans_flow}}

_where_base(where, children=[star])
  → 递归 _compute_base(star) → {col, tables: {trans_flow}}
  → 加 WHERE → {col, tables: {trans_flow}, where: {"trans_status = 'SUCCESS'"}}

prop=aggregate(count, children=[where])
  → 递归 _compute_base(where) → {col, tables, where}
  → 包 COUNT → {col: "COUNT(trans_flow.*)", tables: {trans_flow}, where: {...}}

_groupby_base(groupby, children=[count], group_keys=[dim_domin.region_nm])
  → 路径1: 递归 _compute_base(count) → {col, tables: {trans_flow}, where: {...}}
  → 路径2: 递归 _compute_base(group_key_column) → {tables: {dim_domin, trans_flow},
             joins: [("trans_flow", "region_cd = region_cd")]}
  → 合并: {col: "COUNT(trans_flow.*)",
           tables: {trans_flow, dim_domin},
           joins: [...], where: {...},
           groupby: {"dim_domin.region_nm"}}
```

## 5. groupby 双路径合并

这是唯一采用多路递归的节点。GROUP BY 语义上是查询级操作，包含两类信息：

| 路径 | 来源 | 提供什么 |
|------|------|---------|
| `args[0]` | 被包裹的投影列 (count/sum 等) | col, tables, where, having |
| `group_keys` | GROUP BY 键列 | 额外表、额外 JOIN ON 条件 |

两条路径的 `tables`、`joins`、`where` 取并集（`_merge_joins` 去重保序），确保 SELECT 中同时包含聚合表达式和 GROUP BY 键。

## 6. 组装规则

```
if 节点 prop ∈ merge 且非结构（eq/gt/and/div/...）:
    return col                   ← 纯表达式，直接输出

SELECT: groupby_keys + col       ← groupby 键在前，聚合表达式在后（去重）
FROM:   基表 = col 所属表，否则 tables − join 右表
JOIN:   按 joins 列表顺序逐个 "join {右表} on {on_sql}"
WHERE:  wheres 用 AND 连接
GROUP BY: groupbys
HAVING:  havings
ORDER BY: orderbys
LIMIT:   limit
```

表达式节点走 `merge` prop：`_compute_base` 对 `_child_hashes`（args + casewhen 分支 + window partition/order + 函数命名参数）枚举的每个子节点递归，合并其 tables/where/joins/having/groupby；条件/纯表达式节点在组装层直接返回表达式片段，不包 SELECT。

## 7. 关键设计决策

| 决策 | 为什么 |
|------|--------|
| 分类规约 `_SPEC`，不逐 op 穷举 | `_f` 与 `_compute_base` 曾各自维护一套 op 枚举，已漂移且无法穷尽标量函数 |
| prefix 兜底未知 op | 标量函数唯一变化量是函数名，`OP(args)` 一刀切，新函数零成本 |
| 无 wrap 链，纯递归 | 每个节点已自包含——`_where_base` 递归 `_join_base`→`_leaf_column`，无需向上追溯 |
| 表达式节点 merge 子结构 | eq/gt/round/if 等把子节点携带的 where/joins 向上合并，不截断（修复 Q1526 类 where 丢失） |
| 聚合走 aggregate prop 而非 `_f` | `_f` 是穿透版，会丢失 WHERE 等结构信息 |
| groupby 双路径 | GROUP BY 涉及两类信息源，单路径递归信息不全 |
| `joins` 有序 list | 保留 JOIN 顺序 + ON↔表绑定，避免多表 JOIN 张冠李戴 |
| `field_type` 与 `_SPEC` 正交 | `field_type` 是持久化的语义分类（前端/升级引擎用），`_SPEC` 是运行时渲染分类（仅还原用），二者不融合 |

## 8. L2-04 还原实例

```sql
-- 原始 SQL
SELECT a.trans_id, b.mchnt_nm
FROM trans_flow a LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
```

```
column:  select trans_flow.trans_id from trans_flow
column:  select dim_mchnt.mchnt_nm from dim_mchnt
eq:      dim_mchnt.mchnt_cd = trans_flow.mchnt_cd
join:    select dim_mchnt.mchnt_nm from dim_mchnt
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
_SPEC["new_op"] = ("prefix", None, "merge")   # 标量函数：一行搞定
# 或需要定制渲染时：
_SPEC["new_op"] = ("special", _r_new_op, "merge")
```

标量函数甚至无需注册（prefix 兜底）；只有特殊形状（infix / keyword / 结构 / 特殊渲染）才需要显式声明。

## 11. 与持久化层的关系

```
fields.expression (JSONB)
    ↓ 内容寻址 SHA256[:16] — 不可变

运行时参数传递 (exposed dict)
    ↓ 递归 _compute_base — 纯函数，不持久化

节点分类规约 (_SPEC)
    ↓ Python 代码 — 声明式注册，运行时由 op 推导，不落库
```

`_SPEC` 的 shape/prop 完全由 `op` 决定，属于「读写时推断」，无需持久化；与持久化的语义分类 `field_type` 是正交的两个切面。
