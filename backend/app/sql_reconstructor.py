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

# 每个节点类型：
#   base(hash, exprs, cur) → (sql_fragment, exposed_params_dict)
#   wrap(op_node, downstream_exposed, exprs, cur) → (wrapped_sql_fragment, new_exposed)
# 叶子节点只有 base，结构节点只有 wrap

def _col_base(fh, e, exprs, cur):
    t = e.get("table", "").split(".")[-1]
    return f"{t}.{e.get('column', '')}", {"col": f"{t}.{e.get('column', '')}", "tables": {t}}

def _literal_base(fh, e, exprs, cur):
    v = str(e.get("value", ""))
    s = f"'{v}'" if e.get("type") == "string" else v
    return s, {"col": s, "tables": set()}

def _star_base(fh, e, exprs, cur):
    t = e.get("table", "").split(".")[-1] if e.get("table") else ""
    s = f"{t}.*" if t else "*"
    return s, {"col": s, "tables": {t} if t else set()}

def _ts_base(fh, e, exprs, cur):
    return "CURRENT_TIMESTAMP", {"col": "CURRENT_TIMESTAMP", "tables": set()}

def _cond_base(fh, e, exprs, cur):
    """条件/表达式节点：递归子节点收集表信息"""
    sql = _f(fh, exprs, cur)
    tables = set()
    for a in e.get("args", []):
        h = a.get("hash", "") if isinstance(a, dict) else ""
        if h: tables |= _leaf_tables(h, exprs, cur)
    return sql, {"col": sql, "tables": tables}

# 结构包裹节点

def _join_base(fh, e, exprs, cur):
    """join 节点：递归 args[0] → 列 + on → 条件，合并两条路径"""
    args = e.get("args", [])
    if not args:
        return "?", {"col": "?", "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
    on_h = e.get("on", "")
    if on_h:
        on_sql, on_exp = _compute_base(on_h, exprs, cur)
        on_exp["on"] = {on_sql} | on_exp.get("on", set())
    else:
        on_sql, on_exp = "?", {"col": "?", "tables": set(), "on": set()}
    return child_sql, {
        "col": child_sql,
        "tables": child_exp.get("tables", set()) | on_exp.get("tables", set()),
        "on": child_exp.get("on", set()) | on_exp.get("on", set()),
    }

def _where_base(fh, e, exprs, cur):
    args = e.get("args", [])
    if not args: return "?", {"col": "?", "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
    cond_h = e.get("condition", "")
    cond_sql = _f(cond_h, exprs, cur) if cond_h else "?"
    return child_sql, {
        **child_exp,
        "where": {cond_sql} | child_exp.get("where", set()),
    }

def _groupby_base(fh, e, exprs, cur):
    args = e.get("args", [])
    if not args: return "?", {"col": "?", "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)

    # group_keys 是另一条路径，需要递归获取其中的表/join 信息
    key_sqls = set()
    key_tables = set()
    key_on = set()
    key_where = set()
    for k in e.get("group_keys", []):
        h = k.get("hash", "") if isinstance(k, dict) else k
        if not h: continue
        key_sqls.add(_f(h, exprs, cur))
        # 递归 group key 的 base 获取其包含的表/join/where 信息
        _, key_exp = _compute_base(h, exprs, cur)
        key_tables |= key_exp.get("tables", set())
        key_on |= key_exp.get("on", set())
        key_where |= key_exp.get("where", set())

    return child_sql, {
        "col": child_sql,
        "tables": child_exp.get("tables", set()) | key_tables,
        "on": child_exp.get("on", set()) | key_on,
        "where": child_exp.get("where", set()) | key_where,
        "groupby": key_sqls | child_exp.get("groupby", set()),
    }

def _having_base(fh, e, exprs, cur):
    args = e.get("args", [])
    if not args: return "?", {"col": "?", "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
    cond_h = e.get("condition", "")
    cond_sql = _f(cond_h, exprs, cur) if cond_h else "?"
    return child_sql, {
        **child_exp,
        "having": {cond_sql} | child_exp.get("having", set()),
    }

def _limt_base(fh, e, exprs, cur):
    args = e.get("args", [])
    if not args: return "?", {"col": "?", "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
    result = {**child_exp, "limit": e.get("limit")}
    orders = set()
    for od in e.get("order_by", []):
        if isinstance(od, str): orders.add(_f(od, exprs, cur))
    if orders:
        result["orderby"] = orders | child_exp.get("orderby", set())
    return child_sql, result

# 叶子节点：base
_LEAF_SPEC = {
    "column": _col_base,
    "literal": _literal_base,
    "star": _star_base,
    "current_timestamp": _ts_base,
}

# 条件节点也当叶子处理（它们在 ON/WHERE 中被 _f 递归）
for _op in ("eq", "neq", "gt", "lt", "gte", "lte", "in", "notin",
            "and", "or", "div", "mul", "add", "sub"):
    _LEAF_SPEC[_op] = _cond_base

# 聚合函数 base：递归子节点保留完整上下文
def _agg_base(fh, e, exprs, cur):
    """聚合函数：穿透 args[0]，继承所有下层信息，再包裹 COUNT(...)"""
    args = e.get("args", [])
    if not args:
        sql = _f(fh, exprs, cur)
        return sql, {"col": sql, "tables": set()}
    child_sql, child_exp = _compute_base(args[0]["hash"], exprs, cur)
    op = e.get("op", "count").upper()
    formatted = f"{op}({child_sql})"
    return formatted, {**child_exp, "col": formatted}

for _op in ("count", "sum", "avg", "min", "max"):
    _LEAF_SPEC[_op] = _agg_base

# 结构节点自身可做 base
_LEAF_SPEC["join"] = _join_base
_LEAF_SPEC["where"] = _where_base
_LEAF_SPEC["groupby"] = _groupby_base
_LEAF_SPEC["having"] = _having_base
_LEAF_SPEC["limt"] = _limt_base

# 结构包裹节点：wrap
def _leaf_tables(fh, exprs, cur) -> set[str]:
    """获取节点涉及的所有表名（递归叶子）"""
    e = _load(fh, exprs, cur)
    if not e:
        return set()
    op = e.get("op", "")
    if op in ("column", "star"):
        t = e.get("table", "").split(".")[-1] if e.get("table") else ""
        return {t} if t else set()
    if op in _LEAF_SPEC:
        tables = set()
        for a in e.get("args", []):
            h = a.get("hash", "") if isinstance(a, dict) else ""
            if h:
                tables |= _leaf_tables(h, exprs, cur)
        return tables
    return set()


# ════════════════════════════════════════
# 穿透版 _f（subquery_to_sql 用，不变）
# ════════════════════════════════════════

_COMPARE = {"eq": "=", "neq": "!=", "gt": ">", "lt": "<", "gte": ">=", "lte": "<="}
_ARITH = {"div": "/", "mul": "*", "add": "+", "sub": "-"}
_PENETRATE = {"join", "where", "having", "groupby", "limt"}


def field_to_sql(fh: str) -> str:
    with get_cursor() as cur:
        return _f(fh, {}, cur)


def _f(fh, exprs, cur, aliases=None):
    if aliases and fh in aliases:
        return aliases[fh]
    e = _load(fh, exprs, cur)
    if not e:
        return "?"
    op = e.get("op", "?")
    a2 = aliases

    if op == "column":
        t = e.get("table", "").split(".")[-1]
        return f"{t}.{e.get('column', '')}"
    if op == "literal":
        v = str(e.get("value", ""))
        return f"'{v}'" if e.get("type") == "string" else v
    if op == "star":
        t = e.get("table", "").split(".")[-1] if e.get("table") else ""
        return f"{t}.*" if t else "*"
    if op == "current_timestamp":
        return "CURRENT_TIMESTAMP"
    if op in _PENETRATE:
        a = e.get("args", [])
        return _f(a[0]["hash"], exprs, cur, a2) if a else "?"
    if op == "alias":
        return _f(e["args"][0]["hash"], exprs, cur, a2)
    if op == "ordr":
        inner = _f(e["args"][0]["hash"], exprs, cur, a2)
        return f"{inner} DESC" if e.get("desc") else inner
    if op in ("count", "sum", "avg", "min", "max"):
        return f"{op.upper()}({_child_sql(e, exprs, cur, a2)})"
    if op in _COMPARE:
        a = e.get("args", [])
        if len(a) >= 2:
            left, right = _f(a[0]["hash"], exprs, cur, a2), _f(a[1]["hash"], exprs, cur, a2)
            if exprs.get(a[0]["hash"], {}).get("op") in ("literal",):
                left, right = right, left
            return f"{left} {_COMPARE[op]} {right}"
        return _child_sql(e, exprs, cur, a2)
    if op in ("and", "or"):
        parts = [_f(a["hash"], exprs, cur, a2) for a in e.get("args", []) if isinstance(a, dict)]
        return f" {op.upper()} ".join(parts)
    if op in _ARITH:
        a = e.get("args", [])
        if len(a) >= 2:
            return f"({_f(a[0]['hash'], exprs, cur, a2)} {_ARITH[op]} {_f(a[1]['hash'], exprs, cur, a2)})"
        return _child_sql(e, exprs, cur, a2)
    if op == "in":
        p = [_f(a["hash"], exprs, cur, a2) for a in e.get("args", []) if isinstance(a, dict)]
        return f"{p[0]} IN ({', '.join(p[1:])})"
    if op == "notin":
        p = [_f(a["hash"], exprs, cur, a2) for a in e.get("args", []) if isinstance(a, dict)]
        return f"{p[0]} NOT IN ({', '.join(p[1:])})"
    if op == "round":
        return f"ROUND({_child_sql(e, exprs, cur, a2)}, {e.get('params',{}).get('decimals','0')})"
    if op == "cast":
        return f"CAST({_child_sql(e, exprs, cur, a2)} AS {e.get('params',{}).get('to','STRING')})"
    if op == "nvl":
        return f"NVL({_child_sql(e, exprs, cur, a2)})"
    if op == "if":
        a = [_f(a["hash"], exprs, cur, a2) for a in e.get("args", []) if isinstance(a, dict)]
        p = e.get("params", {})
        return f"IF({a[0] if a else ''}, {p.get('true','')}, {p.get('false','')})"
    if op == "concat":
        return f"CONCAT({_child_sql(e, exprs, cur, a2)})"
    if op == "substring":
        return f"SUBSTRING({_child_sql(e, exprs, cur, a2)})"
    if op == "casewhen":
        parts = []
        for c in e.get("args", []):
            if c.get("condition"):
                parts.append(f"WHEN {_f(c['condition'], exprs, cur, a2)} THEN {_f(c['value'], exprs, cur, a2)}")
            else:
                parts.append(f"ELSE {_f(c['value'], exprs, cur, a2)}")
        return f"CASE {' '.join(parts)} END"
    if op == "row_number":
        parts = ["ROW_NUMBER() OVER ("]
        pb = e.get("partition_by", [])
        ob = e.get("order_by", [])
        if pb:
            parts.append("PARTITION BY " + ", ".join(_f(p["hash"], exprs, cur, a2) for p in pb if isinstance(p, dict)))
        if ob:
            parts.append((" " if pb else "") + "ORDER BY " + ", ".join(_f(o["hash"], exprs, cur, a2) for o in ob if isinstance(o, dict)))
        parts.append(")")
        return " ".join(parts)
    return f"<{op}>"


def _child_sql(e, exprs, cur, aliases=None):
    return ", ".join(_f(a["hash"], exprs, cur, aliases) for a in e.get("args", []) if isinstance(a, dict))


# ════════════════════════════════════════
# 拓扑驱动完整还原
# ════════════════════════════════════════

def field_to_sql_full(fh: str, sq_hash: str | None = None) -> str:
    """将单个字段还原为最小完整 SELECT。

    声明式框架：从叶子节点 base → 逐层向上 wrap，参数由拓扑传递。
    """
    with get_cursor() as cur:
        exprs: dict[str, dict] = {}
        sq_fields: set[str] = set()

        if sq_hash:
            cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (sq_hash,))
            row = cur.fetchone()
            if row:
                _bfs(row["structure"].get("fields", []), exprs, cur)
                sq_fields = set(exprs.keys())

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

        # ── Step 1: 从叶子找 base ──
        _, exposed = _compute_base(fh, exprs, cur)

        # ── Step 2: 组装 SQL ──
        # base 已经递归包含所有下层信息
        # 条件/表达式节点 → 不包 SELECT，直接返回表达式片段
        _COND_OPS = {"eq", "neq", "gt", "lt", "gte", "lte", "in", "notin",
                     "and", "or", "div", "mul", "add", "sub"}
        base_e = _load(fh, exprs, cur)
        if base_e and base_e.get("op") in _COND_OPS:
            return exposed["col"]

        tables = sorted(exposed.get("tables", set()))
        ons = list(exposed.get("on", set()))
        wheres = list(exposed.get("where", set()))
        havings = list(exposed.get("having", set()))
        groupbys = list(exposed.get("groupby", set()))
        orderbys = list(exposed.get("orderby", set()))
        limit = exposed.get("limit")

        # FROM 表优先取列所属表
        base_e = _load(fh, exprs, cur)
        col_table = ""
        if base_e and base_e.get("op") == "column":
            col_table = base_e.get("table", "").split(".")[-1]
        if col_table and col_table in tables:
            tables.remove(col_table)
            tables = [col_table] + sorted(tables)

        # SELECT: groupby keys 在前，col 在后（去重）
        select_cols = sorted(exposed.get("groupby", set()))
        if exposed["col"] not in select_cols:
            select_cols.append(exposed["col"])
        lines = [f"select {', '.join(select_cols)}"]
        lines.append(f"from {tables[0]}" if tables else "from ?")
        for i, t in enumerate(tables[1:]):
            on = ons[i] if i < len(ons) else "?"
            lines.append(f"  join {t} on {on}")

        if wheres:
            lines.append(f"where {' AND '.join(wheres)}")
        if groupbys:
            lines.append(f"group by {', '.join(sorted(groupbys))}")
        if havings:
            lines.append(f"having {' AND '.join(havings)}")
        if orderbys:
            lines.append(f"order by {', '.join(orderbys)}")
        if limit is not None:
            lines.append(f"limit {limit}")

        return "\n".join(lines)


def _compute_base(fh, exprs, cur):
    """递归计算叶子节点的 base 和 exposed"""
    e = _load(fh, exprs, cur)
    if not e:
        return "?", {"col": "?", "tables": set()}
    op = e.get("op", "")

    fn = _LEAF_SPEC.get(op)
    if fn:
        return fn(fh, e, exprs, cur)

    # 穿透到 args[0]：alias 等非结构节点
    if op == "alias":
        return _compute_base(e["args"][0]["hash"], exprs, cur)

    # 其他：alias → 穿透
    if op == "alias":
        return _compute_base(e["args"][0]["hash"], exprs, cur)

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
        indented = "\n".join("  " + l for l in inner_sql.split("\n"))
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
    having_cond = _first_having(roots, exprs, cur)
    order_exprs = _collect_orders(roots, exprs, cur)
    limit_val = _collect_limit(roots, exprs)

    lines = [f"SELECT\n  " + ",\n  ".join(select_parts),
             f"FROM {from_clause}"]
    lines.extend(join_clauses)
    if where_cond: lines.append(f"WHERE {where_cond}")
    if group_keys: lines.append(f"GROUP BY {', '.join(group_keys)}")
    if having_cond: lines.append(f"HAVING {having_cond}")
    if order_exprs: lines.append(f"ORDER BY " + ", ".join(order_exprs))
    if limit_val is not None: lines.append(f"LIMIT {limit_val}")
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
                visited.add(h); extra.append(h)
        for gk in e.get("group_keys", []):
            h = gk.get("hash", "") if isinstance(gk, dict) else gk
            if h and h not in visited:
                visited.add(h); extra.append(h)
        for od in e.get("order_by", []):
            if isinstance(od, str) and od not in visited:
                visited.add(od); extra.append(od)
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
        if not e: return
        if e.get("op") == "join":
            on = e.get("on")
            if on and on not in seen:
                seen.add(on); ons.append(_f(on, exprs, cur))
        for a in e.get("args", []):
            c = a.get("hash","") if isinstance(a, dict) else ""
            if c: w(c)
    for r in roots: w(r)
    return ons


def _first_where(roots, exprs, cur):
    for r in roots:
        e = exprs.get(r, {})
        if e.get("op") == "where":
            cond = e.get("condition", "")
            if cond: return _f(cond, exprs, cur)
    return None


def _first_having(roots, exprs, cur):
    for r in roots:
        e = exprs.get(r, {})
        if e.get("op") == "having":
            cond = e.get("condition", "")
            if cond: return _f(cond, exprs, cur)
    return None


def _collect_keys(roots, exprs, cur):
    keys, seen = [], set()
    def w(fh):
        e = exprs.get(fh)
        if not e: return
        if e.get("op") == "groupby":
            for gk in e.get("group_keys", []):
                h = gk.get("hash","") if isinstance(gk, dict) else gk
                if h and h not in seen:
                    seen.add(h); keys.append(_f(h, exprs, cur))
        for a in e.get("args", []):
            c = a.get("hash","") if isinstance(a, dict) else ""
            if c: w(c)
    for r in roots: w(r)
    return keys


def _collect_orders(roots, exprs, cur):
    orders, seen = [], set()
    def w(fh):
        e = exprs.get(fh)
        if not e: return
        if e.get("op") == "limt":
            for od in e.get("order_by", []):
                if isinstance(od, str) and od not in seen:
                    seen.add(od); orders.append(_f(od, exprs, cur))
        for a in e.get("args", []):
            c = a.get("hash","") if isinstance(a, dict) else ""
            if c: w(c)
    for r in roots: w(r)
    return orders


def _collect_limit(roots, exprs):
    for r in roots:
        e = exprs.get(r, {})
        if e.get("op") == "limt":
            return e.get("limit")
    return None
