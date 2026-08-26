"""字段 DAG → SQL 还原

三层：
  1. 声明式节点规约 NODE_SPEC  — 每种节点声明 receives/exposes/build
  2. field_to_sql(hash)         — 穿透版（subquery_to_sql 用）
  3. field_to_sql_full(hash, sq) — 拓扑驱动完整还原（前端人类可读）
"""

import json as _json
from app.database import get_cursor

# ════════════════════════════════════════
# 声明式节点规约
# ════════════════════════════════════════

# 每个 op → (shape, param, prop)：
#   shape 渲染形状（_f 消费）  atomic / penetrate / cmp / arith / keyword / prefix / special
#   param shape 参数：atomic/special 传函数，cmp/arith 传符号，keyword 传关键字，其余 None
#   prop  结构传播（_compute_base 消费）  leaf / inherit / merge / aggregate / structural

def _leaf_column(e, exprs, cur):
    t = e.get("table", "").split(".")[-1]
    col = f"{t}.{e.get('column', '')}"
    return col, {"col": col, "tables": {t}}


def _leaf_literal(e, exprs, cur):
    v = str(e.get("value", ""))
    col = f"'{v}'" if e.get("type") == "string" else v
    return col, {"col": col, "tables": set()}


def _leaf_star(e, exprs, cur):
    t = e.get("table", "").split(".")[-1] if e.get("table") else ""
    col = f"{t}.*" if t else "*"
    return col, {"col": col, "tables": {t} if t else set()}


def _leaf_ts(e, exprs, cur):
    return "CURRENT_TIMESTAMP", {"col": "CURRENT_TIMESTAMP", "tables": set()}


# ── 特殊渲染函数（返回 SQL 片段，消费 child_cols，不再递归）──

def _r_in(e, child_cols, exprs, cur):
    kw = "NOT IN" if e.get("op") == "notin" else "IN"
    p = [child_cols.get(a["hash"], "?") for a in e.get("args", []) if isinstance(a, dict)]
    return f"{p[0]} {kw} ({', '.join(p[1:])})"


def _r_cast(e, child_cols, exprs, cur):
    inner = ", ".join(child_cols.get(a["hash"], "?") for a in e.get("args", []) if isinstance(a, dict))
    return f"CAST({inner} AS {e.get('params', {}).get('to', 'STRING')})"


def _r_round(e, child_cols, exprs, cur):
    inner = ", ".join(child_cols.get(a["hash"], "?") for a in e.get("args", []) if isinstance(a, dict))
    return f"ROUND({inner}, {e.get('params', {}).get('decimals', '0')})"


def _r_if(e, child_cols, exprs, cur):
    a = [child_cols.get(x["hash"], "?") for x in e.get("args", []) if isinstance(x, dict)]
    p = e.get("params", {})
    true_sql = child_cols.get(p.get("true", ""), p.get("true", ""))
    false_sql = child_cols.get(p.get("false", ""), p.get("false", ""))
    return f"IF({a[0] if a else ''}, {true_sql}, {false_sql})"


def _r_case(e, child_cols, exprs, cur):
    parts = []
    for c in e.get("args", []):
        if c.get("condition"):
            parts.append(f"WHEN {child_cols.get(c['condition'], '?')} THEN {child_cols.get(c['value'], '?')}")
        else:
            parts.append(f"ELSE {child_cols.get(c['value'], '?')}")
    return f"CASE {' '.join(parts)} END"


def _r_window(e, child_cols, exprs, cur):
    inner = child_cols.get(e["args"][0]["hash"], "?") if e.get("args") else ""
    parts = [inner, "OVER ("]
    pb = e.get("partition_by", [])
    ob = e.get("order_by", [])
    frame = e.get("frame", "")
    if pb:
        parts.append("PARTITION BY " + ", ".join(child_cols.get(p["hash"], "?") for p in pb if isinstance(p, dict)))
    if ob:
        parts.append((" " if pb else "") + "ORDER BY " + ", ".join(child_cols.get(o["hash"], "?") for o in ob if isinstance(o, dict)))
    if frame:
        parts.append((" " if (pb or ob) else "") + frame)
    parts.append(")")
    return " ".join(parts)


def _r_ordr(e, child_cols, exprs, cur):
    inner = child_cols.get(e["args"][0]["hash"], "?")
    return f"{inner} DESC" if e.get("desc") else inner


def _r_paren(e, child_cols, exprs, cur):
    inner = child_cols.get(e["args"][0]["hash"], "?") if e.get("args") else ""
    return f"({inner})"


# 结构包裹节点

def _join_base(fh, e, exprs, cur):
    """join 节点：递归 args[0] → 列 + on → 条件，合并两条路径"""
    args = e.get("args", [])
    if not args:
        return "?", {"col": "?", "tables": set(), "joins": []}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
    joins = list(child_exp.get("joins", []))
    on_h = e.get("on", "")
    if on_h:
        on_sql, on_exp = _compute_base(on_h, exprs, cur)
        # 本层 JOIN 引入的右表 = ON 条件涉及的表 − 子节点已累积的表
        new_tables = on_exp.get("tables", set()) - child_exp.get("tables", set())
        right_table = sorted(new_tables)[0] if new_tables else "?"
        joins.append((right_table, on_sql))
        on_tables = on_exp.get("tables", set())
    else:
        on_tables = set()
    return child_sql, {
        "col": child_sql,
        "tables": child_exp.get("tables", set()) | on_tables,
        "joins": joins,
    }

def _where_base(fh, e, exprs, cur):
    args = e.get("args", [])
    if not args:
        return "?", {"col": "?", "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
    cond_h = e.get("condition", "")
    if not cond_h:
        return child_sql, child_exp
    cond_sql, cond_exp = _compute_base(cond_h, exprs, cur)
    result = _merge_exposed(child_exp, cond_exp)
    result["where"] = {cond_sql} | result.get("where", set())
    return child_sql, result

def _groupby_base(fh, e, exprs, cur):
    args = e.get("args", [])
    if not args:
        return "?", {"col": "?", "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)

    # group_keys 是另一条路径，需要递归获取其中的表/join 信息
    key_sqls = set()
    key_tables = set()
    key_joins = []
    key_where = set()
    for k in e.get("group_keys", []):
        h = k.get("hash", "") if isinstance(k, dict) else k
        if not h:
            continue
        key_sqls.add(_f(h, exprs, cur))
        # 递归 group key 的 base 获取其包含的表/join/where 信息
        _, key_exp = _compute_base(h, exprs, cur)
        key_tables |= key_exp.get("tables", set())
        key_joins.extend(key_exp.get("joins", []))
        key_where |= key_exp.get("where", set())

    return child_sql, {
        "col": child_sql,
        "tables": child_exp.get("tables", set()) | key_tables,
        "joins": _merge_joins(child_exp.get("joins", []), key_joins),
        "where": child_exp.get("where", set()) | key_where,
        "groupby": key_sqls | child_exp.get("groupby", set()),
    }

def _limt_base(fh, e, exprs, cur):
    args = e.get("args", [])
    if not args:
        return "?", {"col": "?", "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
    result = {**child_exp, "limit": e.get("limit")}
    orders = set()
    for od in e.get("order_by", []):
        if isinstance(od, str):
            orders.add(_f(od, exprs, cur))
    if orders:
        result["orderby"] = orders | child_exp.get("orderby", set())
    return child_sql, result

# ── 规约表：op → (shape, param, prop) ──

_SPEC = {
    # 叶子
    "column": ("atomic", _leaf_column, "leaf"),
    "literal": ("atomic", _leaf_literal, "leaf"),
    "star": ("atomic", _leaf_star, "leaf"),
    "current_timestamp": ("atomic", _leaf_ts, "leaf"),
    # 穿透（alias 在 _compute_base 透明，AS 名由 field_to_sql_full 顶层体现）
    "alias": ("penetrate", None, "inherit"),
    "ordr": ("special", _r_ordr, "merge"),
    "paren": ("special", _r_paren, "merge"),
    # 比较（cmp：无括号 + 字面量右移）
    "eq": ("cmp", "=", "merge"),
    "neq": ("cmp", "!=", "merge"),
    "gt": ("cmp", ">", "merge"),
    "lt": ("cmp", "<", "merge"),
    "gte": ("cmp", ">=", "merge"),
    "lte": ("cmp", "<=", "merge"),
    "like": ("cmp", "LIKE", "merge"),
    "ilike": ("cmp", "ILIKE", "merge"),
    "is": ("cmp", "IS", "merge"),
    "isdistinctfrom": ("cmp", "IS DISTINCT FROM", "merge"),
    "similarto": ("cmp", "SIMILAR TO", "merge"),
    # 算术（arith：带括号）
    "div": ("arith", "/", "merge"),
    "mul": ("arith", "*", "merge"),
    "add": ("arith", "+", "merge"),
    "sub": ("arith", "-", "merge"),
    # 逻辑
    "and": ("keyword", "AND", "merge"),
    "or": ("keyword", "OR", "merge"),
    "xor": ("keyword", "XOR", "merge"),
    "not": ("unary", "NOT", "merge"),
    # 列表
    "in": ("special", _r_in, "merge"),
    "notin": ("special", _r_in, "merge"),
    # 聚合
    "count": ("prefix", None, "aggregate"),
    "sum": ("prefix", None, "aggregate"),
    "avg": ("prefix", None, "aggregate"),
    "min": ("prefix", None, "aggregate"),
    "max": ("prefix", None, "aggregate"),
    # 特殊
    "cast": ("special", _r_cast, "merge"),
    "round": ("special", _r_round, "merge"),
    "if": ("special", _r_if, "merge"),
    "casewhen": ("special", _r_case, "merge"),
    "window": ("special", _r_window, "merge"),
    "row_number": ("prefix", None, "merge"),
    # 结构（_f 穿透 args[0]，_compute_base 走 _STRUCTURAL）
    "join": ("penetrate", None, "structural"),
    "where": ("penetrate", None, "structural"),
    "groupby": ("penetrate", None, "structural"),
    "limt": ("penetrate", None, "structural"),
}

# 未知 op → 一律按标量函数（prefix：OP(args)），彻底消灭标量函数穷举
_DEFAULT_SPEC = ("prefix", None, "merge")

_STRUCTURAL = {
    "join": _join_base,
    "where": _where_base,
    "groupby": _groupby_base,
    "limt": _limt_base,
}


def _spec(op):
    return _SPEC.get(op, _DEFAULT_SPEC)

# 结构包裹节点：wrap
def _child_hashes(e) -> list[str]:
    """提取节点表达式中所有子节点 hash（args + casewhen 分支 + window partition/order + 函数命名参数）"""
    out = []
    for a in e.get("args", []):
        if isinstance(a, dict):
            if a.get("hash"):
                out.append(a["hash"])
            # casewhen 分支：{condition, value}
            if a.get("condition"):
                out.append(a["condition"])
            if a.get("value"):
                out.append(a["value"])
    for pb in e.get("partition_by", []):
        if isinstance(pb, dict) and pb.get("hash"):
            out.append(pb["hash"])
    for ob in e.get("order_by", []):
        if isinstance(ob, dict) and ob.get("hash"):
            out.append(ob["hash"])
    # 函数命名参数中的 hash 引用（如 if 的 true/false）
    for v in (e.get("params") or {}).values():
        if isinstance(v, str) and len(v) == 16:
            out.append(v)
    return out


def _merge_joins(*join_lists):
    """按序合并 join 序列，去重 (table, on_sql) 对"""
    seen, out = set(), []
    for lst in join_lists:
        for pair in lst:
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    return out


def _merge_exposed(base, extra):
    """合并两个 exposed dict：tables/where/groupby 取并集，joins 去重保序"""
    result = dict(base)
    result["tables"] = base.get("tables", set()) | extra.get("tables", set())
    result["where"] = base.get("where", set()) | extra.get("where", set())
    result["groupby"] = base.get("groupby", set()) | extra.get("groupby", set())
    result["joins"] = _merge_joins(base.get("joins", []), extra.get("joins", []))
    return result


# ════════════════════════════════════════
# 穿透版 _f（subquery_to_sql 用，不变）
# ════════════════════════════════════════

def field_to_sql(fh: str) -> str:
    with get_cursor() as cur:
        return _f(fh, {}, cur)


def _render(e, child_cols, exprs, cur):
    """用子节点已算好的 col 组装本节点 SQL 片段（不递归，消费 child_cols）。_f 与 _compute_base 共用。"""
    op = e.get("op", "?")
    shape, param, _prop = _spec(op)
    args = e.get("args", [])

    def C(h):
        return child_cols.get(h, "?")

    if shape == "atomic":
        return param(e, exprs, cur)[0]
    if shape == "special":
        return param(e, child_cols, exprs, cur)
    if shape == "penetrate":
        return C(args[0]["hash"]) if args else "?"
    if shape == "cmp":
        if len(args) >= 2:
            left = C(args[0]["hash"])
            right = C(args[1]["hash"])
            if exprs.get(args[0]["hash"], {}).get("op") == "literal":
                left, right = right, left
            return f"{left} {param} {right}"
        return ", ".join(C(a["hash"]) for a in args if isinstance(a, dict))
    if shape == "arith":
        if len(args) >= 2:
            return f"({C(args[0]['hash'])} {param} {C(args[1]['hash'])})"
        return ", ".join(C(a["hash"]) for a in args if isinstance(a, dict))
    if shape == "keyword":
        return f" {param} ".join(C(a["hash"]) for a in args if isinstance(a, dict))
    if shape == "unary":
        return f"{param} {C(args[0]['hash'])}" if args else param
    if shape == "prefix":
        return f"{op.upper()}({', '.join(C(a['hash']) for a in args if isinstance(a, dict))})"
    return f"<{op}>"


def _f(fh, exprs, cur, aliases=None):
    """穿透版渲染：先递归算子节点 col，再用 _render 组装"""
    if aliases and fh in aliases:
        return aliases[fh]
    e = _load(fh, exprs, cur)
    if not e:
        return "?"
    child_cols = {h: _f(h, exprs, cur, aliases) for h in _child_hashes(e)}
    return _render(e, child_cols, exprs, cur)


# ════════════════════════════════════════
# 拓扑驱动完整还原
# ════════════════════════════════════════

# 条件/表达式节点（作为完整查询时投影用占位符，由下游替换）
_COND_OPS = {"eq", "neq", "gt", "lt", "gte", "lte", "in", "notin",
             "and", "or", "div", "mul", "add", "sub"}
_PLACEHOLDER = "__SQ_PROJ__"


def _assemble_select(exposed, projection, col_table=""):
    """把 exposed 结构上下文组装成最小 SELECT 的行列表。

    projection: SELECT 的投影列（可为占位符）；col_table: 字段所属表（优先作 FROM 基表）。
    """
    tables = sorted(exposed.get("tables", set()))
    joins = list(exposed.get("joins", []))
    join_tables = {jt for jt, _ in joins}
    wheres = list(exposed.get("where", set()))
    groupbys = list(exposed.get("groupby", set()))
    orderbys = list(exposed.get("orderby", set()))
    limit = exposed.get("limit")

    if col_table and col_table in tables:
        base_table = col_table
    else:
        leftover = sorted(set(tables) - join_tables)
        base_table = leftover[0] if leftover else (tables[0] if tables else "?")

    # SELECT: groupby keys 在前，projection 在后（去重）
    select_cols = sorted(exposed.get("groupby", set()))
    if projection not in select_cols:
        select_cols.append(projection)
    lines = [f"select {', '.join(select_cols)}"]
    lines.append(f"from {base_table}" if tables else "from ?")
    for right_table, on_sql in joins:
        lines.append(f"  join {right_table} on {on_sql}")

    if wheres:
        lines.append(f"where {' AND '.join(wheres)}")
    if groupbys:
        lines.append(f"group by {', '.join(sorted(groupbys))}")
    if orderbys:
        lines.append(f"order by {', '.join(orderbys)}")
    if limit is not None:
        lines.append(f"limit {limit}")

    return lines


def _condition_query(fh, exprs, cur, col, exposed):
    """条件节点的完整查询：grouped（聚合/分组）操作数展开为子查询，条件引用其输出列。

    无 grouped 操作数时退化为平铺条件进 where（简单列/字面量比较）。
    """
    e = _load(fh, exprs, cur)
    subqueries = []  # [(alias, subquery_sql)]
    ref_map = {}     # child_hash -> 引用串

    for h in _child_hashes(e):
        child_col, child_exp = _compute_base(h, exprs, cur)
        if child_exp.get("groupby"):
            child_e = _load(h, exprs, cur)
            alias = f"sub{len(subqueries)}"
            if child_e.get("op") == "alias" and child_e.get("alias_name"):
                output_col = child_e["alias_name"]
            else:
                output_col = child_col
            sub_sql = _field_query(h, exprs, cur)
            subqueries.append((alias, sub_sql))
            ref_map[h] = f"{alias}.{output_col}"
        else:
            ref_map[h] = child_col

    cond_sql = _render(e, ref_map, exprs, cur)

    if not subqueries:
        # 简单条件：投影候选 = 非字面量操作数
        proj_candidates = [ref_map[h] for h in _child_hashes(e) if exprs.get(h, {}).get("op") != "literal"]
        projection = ", ".join(proj_candidates) if proj_candidates else _PLACEHOLDER
        exposed = dict(exposed)
        exposed["where"] = {col} | exposed.get("where", set())
        return "\n".join(_assemble_select(exposed, projection, ""))

    # grouped 条件：投影候选 = 枚举（group keys + 操作数输出列），供下游选择
    enum_cols = []
    for gk in sorted(exposed.get("groupby", set())):
        enum_cols.append(gk.split(".")[-1])
    for h in _child_hashes(e):
        if exprs.get(h, {}).get("op") != "literal":
            enum_cols.append(ref_map[h].split(".")[-1])
    deduped = []
    for c in enum_cols:
        if c not in deduped:
            deduped.append(c)
    projection = f"__PROJ_ENUM__[{','.join(deduped)}]"

    lines = [f"select {projection}"]
    from_parts = []
    for alias, sub_sql in subqueries:
        indented = "\n".join("  " + line for line in sub_sql.split("\n"))
        from_parts.append(f"(\n{indented}\n) {alias}")
    lines.append("from " + ", ".join(from_parts))
    lines.append(f"where {cond_sql}")
    return "\n".join(lines)


def _is_grouped(h, exprs, cur):
    """字段是否分组/聚合（exposed 带 groupby）。"""
    _, child_exp = _compute_base(h, exprs, cur)
    return bool(child_exp.get("groupby"))


def _where_query(fh, exprs, cur):
    """where 包裹 group 过滤：去糖成「子查询 + WHERE」。

    子查询 = 合并「投影聚合 + 条件引用的聚合」的聚合查询，条件引用子查询输出列。
    """
    e = _load(fh, exprs, cur)
    proj_h = e["args"][0]["hash"]
    cond_h = e.get("condition", "")
    cond_e = _load(cond_h, exprs, cur)

    alias = "sub0"
    seed_map = {}
    agg_cols = []   # 子查询 SELECT 列（含别名）
    sub_exp = None  # 子查询 exposed（取自第一个聚合）
    seen = set()

    def register_agg(h):
        nonlocal sub_exp
        if h in seen:
            return
        seen.add(h)
        h_e = _load(h, exprs, cur)
        h_col, h_exp = _compute_base(h, exprs, cur)
        if h_e.get("op") == "alias" and h_e.get("alias_name"):
            out_col = h_e["alias_name"]
        else:
            out_col = f"_agg{len(agg_cols)}"
        if sub_exp is None:
            sub_exp = h_exp
        agg_cols.append(f"{h_col} AS {out_col}")
        seed_map[h] = f"{alias}.{out_col}"
        if h_e.get("op") == "alias" and h_e.get("args"):
            inner_h = h_e["args"][0]["hash"]
            seed_map[inner_h] = f"{alias}.{out_col}"
            seen.add(inner_h)

    # 投影是聚合则纳入，再纳入条件引用的聚合
    if _is_grouped(proj_h, exprs, cur):
        register_agg(proj_h)
    for h in _child_hashes(cond_e):
        if _is_grouped(h, exprs, cur):
            register_agg(h)

    sub_sql = "\n".join(_assemble_select(sub_exp or {}, ", ".join(agg_cols), ""))

    # 本字段在子查询里的输出引用（占位符在此还原为实际投影）
    if proj_h in seed_map:
        projection = seed_map[proj_h]
    else:
        proj_col = _compute_base(proj_h, exprs, cur)[0]
        projection = f"{alias}.{proj_col.split('.')[-1]}"

    ref_map = {}
    for h in _child_hashes(cond_e):
        if h in seed_map:
            ref_map[h] = seed_map[h]
        else:
            ref_map[h] = _compute_base(h, exprs, cur)[0]
    cond_sql = _render(cond_e, ref_map, exprs, cur)

    indented = "\n".join("  " + line for line in sub_sql.split("\n"))
    return "\n".join([
        f"select {projection}",
        f"from (\n{indented}\n) {alias}",
        f"where {cond_sql}",
    ])


def _field_query(fh, exprs, cur, projection=None):
    """递归生成字段的最小完整查询。

    projection=None 时按节点类型决定：条件/表达式走 _condition_query，alias 用 `col AS name`，其余用字段 col。
    """
    col, exposed = _compute_base(fh, exprs, cur)
    base_e = _load(fh, exprs, cur)
    op = base_e.get("op", "") if base_e else ""

    if projection is None:
        if op in _COND_OPS:
            return _condition_query(fh, exprs, cur, col, exposed)
        elif op == "where" and base_e.get("condition") and _is_grouped(base_e["condition"], exprs, cur):
            return _where_query(fh, exprs, cur)
        elif op == "alias" and base_e.get("alias_name"):
            projection = f"{col} AS {base_e['alias_name']}"
        else:
            projection = col

    col_table = base_e.get("table", "").split(".")[-1] if op == "column" else ""
    return "\n".join(_assemble_select(exposed, projection, col_table))


def field_to_sql_full(fh: str, sq_hash: str | None = None) -> str:
    """将单个字段还原为最小完整 SELECT（薄封装，递归组装在 _field_query）。"""
    with get_cursor() as cur:
        exprs: dict[str, dict] = {}

        if sq_hash:
            cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (sq_hash,))
            row = cur.fetchone()
            if row:
                _bfs(row["structure"].get("fields", []), exprs, cur)

        _bfs([fh], exprs, cur)
        # 额外引用
        extra = []
        for _, e in list(exprs.items()):
            for key in ("condition", "on"):
                h = e.get(key)
                if h and h not in exprs:
                    extra.append(h)
        for h in extra:
            _bfs([h], exprs, cur)

        return _field_query(fh, exprs, cur)


def _compute_base(fh, exprs, cur):
    """递归计算字段的 base（SQL 片段 + exposed）。读 _SPEC 的 prop 决定结构传播。"""
    e = _load(fh, exprs, cur)
    if not e:
        return "?", {"col": "?", "tables": set()}
    op = e.get("op", "")
    _shape, param, prop = _spec(op)

    if prop == "leaf":
        return param(e, exprs, cur)
    if prop == "inherit":
        return _compute_base(e["args"][0]["hash"], exprs, cur)
    if prop == "merge":
        child_cols = {}
        tables = set()
        wheres = set()
        joins = []
        groupbys = set()
        for h in _child_hashes(e):
            child_col, child_exp = _compute_base(h, exprs, cur)
            child_cols[h] = child_col
            tables |= child_exp.get("tables", set())
            wheres |= child_exp.get("where", set())
            joins = _merge_joins(joins, child_exp.get("joins", []))
            groupbys |= child_exp.get("groupby", set())
        col = _render(e, child_cols, exprs, cur)
        return col, {
            "col": col,
            "tables": tables,
            "where": wheres,
            "joins": joins,
            "groupby": groupbys,
        }
    if prop == "aggregate":
        args = e.get("args", [])
        if not args:
            col = _render(e, {}, exprs, cur)
            return col, {"col": col, "tables": set()}
        child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
        formatted = f"{op.upper()}({child_sql})"
        return formatted, {**child_exp, "col": formatted}
    if prop == "structural":
        return _STRUCTURAL[op](fh, e, exprs, cur)
    return f"<{op}>", {"col": f"<{op}>", "tables": set()}


# ════════════════════════════════════════
# subquery → SQL（不变）
# ════════════════════════════════════════

def subquery_to_sql(sq_hash: str) -> str:
    with get_cursor() as cur:
        cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (sq_hash,))
        row = cur.fetchone()
        if not row:
            return "-- subquery not found"
        struct = row["structure"]
        if struct.get("type") == "union":
            return _union_sql(struct, cur)
        return _select_sql(sq_hash, struct, cur)


def _select_sql(sq_hash, struct, cur):
    roots = struct.get("fields", [])
    alias_map = struct.get("alias_map", {})
    rev = {v: k for k, v in alias_map.items()}
    inner_sqs = struct.get("inner_subqueries", [])

    exprs: dict[str, dict] = {}
    _bfs(roots, exprs, cur)

    field_aliases: dict[str, str] = {}
    subq_alias = ""
    if inner_sqs:
        subq_alias = "sub"
        cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (inner_sqs[0],))
        inner_struct = cur.fetchone()["structure"]
        for alias, fh in inner_struct.get("alias_map", {}).items():
            field_aliases[fh] = f"{subq_alias}.{alias}"

    select_parts = []
    for root_h in roots:
        alias = rev.get(root_h, "")
        if subq_alias and alias:
            sql = f"{subq_alias}.{alias}"
        else:
            sql = _f(root_h, exprs, cur)
        if alias:
            sql = f"{sql} AS {alias}"
        select_parts.append(sql)

    tables = sorted(_collect_tables(exprs))
    if subq_alias and inner_sqs:
        inner_sql = subquery_to_sql(inner_sqs[0])
        indented = "\n".join("  " + line for line in inner_sql.split("\n"))
        from_clause = f"(\n{indented}\n) AS {subq_alias}"
        join_clauses = []
    elif tables:
        from_clause = tables[0]
        ons = _collect_ons(roots, exprs, cur)
        join_clauses = [f"  JOIN {t} ON {ons[i]}" for i, t in enumerate(tables[1:]) if i < len(ons)]
    else:
        from_clause = "?"
        join_clauses = []

    where_cond = _first_where(roots, exprs, cur)
    group_keys = _collect_keys(roots, exprs, cur)
    order_exprs = _collect_orders(roots, exprs, cur)
    limit_val = _collect_limit(roots, exprs)

    lines = ["SELECT\n  " + ",\n  ".join(select_parts),
             f"FROM {from_clause}"]
    lines.extend(join_clauses)
    if where_cond:
        lines.append(f"WHERE {where_cond}")
    if group_keys:
        lines.append(f"GROUP BY {', '.join(group_keys)}")
    if order_exprs:
        lines.append("ORDER BY " + ", ".join(order_exprs))
    if limit_val is not None:
        lines.append(f"LIMIT {limit_val}")
    return "\n".join(lines)


def _union_sql(struct, cur):
    branches = struct.get("branches", [])
    op = "UNION" if struct.get("distinct", False) else "UNION ALL"
    return f"\n{op}\n".join(subquery_to_sql(bh) for bh in branches)


# ═══════════════════════ 辅助 ═══════════════════

def _load(fh, exprs, cur):
    if fh not in exprs:
        cur.execute("SELECT expression FROM fields WHERE hash = %s", (fh,))
        row = cur.fetchone()
        if row:
            e = row["expression"]
            exprs[fh] = _json.loads(e) if isinstance(e, str) else e
    return exprs.get(fh)


def _bfs(roots, exprs, cur):
    queue = list(roots)
    visited = set(queue)
    while queue:
        fh = queue.pop(0)
        _load(fh, exprs, cur)
        cur.execute("SELECT referenced_hash FROM field_references WHERE referrer_hash = %s", (fh,))
        for r in cur.fetchall():
            if r["referenced_hash"] not in visited:
                visited.add(r["referenced_hash"])
                queue.append(r["referenced_hash"])
    extra = []
    for fh, e in list(exprs.items()):
        for key in ("condition", "on"):
            h = e.get(key)
            if h and h not in visited:
                visited.add(h)
                extra.append(h)
        for gk in e.get("group_keys", []):
            h = gk.get("hash", "") if isinstance(gk, dict) else gk
            if h and h not in visited:
                visited.add(h)
                extra.append(h)
        for od in e.get("order_by", []):
            if isinstance(od, str) and od not in visited:
                visited.add(od)
                extra.append(od)
    while extra:
        fh = extra.pop(0)
        _load(fh, exprs, cur)
        cur.execute("SELECT referenced_hash FROM field_references WHERE referrer_hash = %s", (fh,))
        for r in cur.fetchall():
            if r["referenced_hash"] not in visited:
                visited.add(r["referenced_hash"])
                extra.append(r["referenced_hash"])


def _collect_tables(exprs):
    return {e["table"].split(".")[-1] for e in exprs.values() if e.get("op") == "column" and e.get("table")}


def _collect_ons(roots, exprs, cur):
    ons, seen = [], set()

    def w(fh):
        e = exprs.get(fh)
        if not e:
            return
        if e.get("op") == "join":
            on = e.get("on")
            if on and on not in seen:
                seen.add(on)
                ons.append(_f(on, exprs, cur))
        for a in e.get("args", []):
            c = a.get("hash", "") if isinstance(a, dict) else ""
            if c:
                w(c)

    for r in roots:
        w(r)
    return ons


def _first_where(roots, exprs, cur):
    for r in roots:
        e = exprs.get(r, {})
        if e.get("op") == "where":
            cond = e.get("condition", "")
            if cond:
                return _f(cond, exprs, cur)
    return None


def _collect_keys(roots, exprs, cur):
    keys, seen = [], set()

    def w(fh):
        e = exprs.get(fh)
        if not e:
            return
        if e.get("op") == "groupby":
            for gk in e.get("group_keys", []):
                h = gk.get("hash", "") if isinstance(gk, dict) else gk
                if h and h not in seen:
                    seen.add(h)
                    keys.append(_f(h, exprs, cur))
        for a in e.get("args", []):
            c = a.get("hash", "") if isinstance(a, dict) else ""
            if c:
                w(c)

    for r in roots:
        w(r)
    return keys


def _collect_orders(roots, exprs, cur):
    orders, seen = [], set()

    def w(fh):
        e = exprs.get(fh)
        if not e:
            return
        if e.get("op") == "limt":
            for od in e.get("order_by", []):
                if isinstance(od, str) and od not in seen:
                    seen.add(od)
                    orders.append(_f(od, exprs, cur))
        for a in e.get("args", []):
            c = a.get("hash", "") if isinstance(a, dict) else ""
            if c:
                w(c)

    for r in roots:
        w(r)
    return orders


def _collect_limit(roots, exprs):
    for r in roots:
        e = exprs.get(r, {})
        if e.get("op") == "limt":
            return e.get("limit")
    return None
