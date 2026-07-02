"""Phase 3 字段构建 — 递归遍历 AST 生成字段节点"""

import sqlglot.expressions as exp

from app.lineage_engine.context import BuildContext, compute_hash


class UnsupportedExpression(Exception):
    pass


class HashMismatch(Exception):
    pass


class MissingLineageData(Exception):
    pass


def build_field(node: exp.Expression, ctx: BuildContext) -> str:
    cache_key = node.sql()
    # 叶子节点不缓存 —— 它们的值依赖上下文（别名解析、group_keys 等）
    _leaf_types = (exp.Column, exp.Literal, exp.Null, exp.Star, exp.Table, exp.Select, exp.Union, exp.Alias)
    if not isinstance(node, _leaf_types):
        if ctx.group_keys:
            cache_key = f"{cache_key}|gk:{','.join(ctx.group_keys)}"
        if cache_key in ctx.cache:
            return ctx.cache[cache_key]

    if isinstance(node, exp.Literal):
        result = build_literal(node, ctx)
    elif isinstance(node, exp.Column):
        result = build_column(node, ctx)
    elif isinstance(node, (exp.Binary, exp.Unary)):
        result = build_expression(node, ctx)
    elif isinstance(node, exp.In):
        result = build_in(node, ctx)
    elif isinstance(node, (exp.Cast,)):
        result = build_cast(node, ctx)
    elif isinstance(node, exp.Case):
        result = build_case_when(node, ctx)
    elif isinstance(node, exp.Alias):
        result = build_alias(node, ctx)
    elif isinstance(node, (
        exp.Anonymous, exp.Coalesce, exp.Concat,
        exp.Round, exp.Abs, exp.Ceil, exp.Floor,
        exp.Length, exp.Lower, exp.Upper, exp.Trim,
        exp.Substring, exp.RegexpReplace,
        exp.If, exp.Nullif,
        exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max,
        exp.RowNumber,
        exp.Explode, exp.Split,
    )):
        result = build_function(node, ctx)
    elif isinstance(node, exp.Where):
        result = build_field(node.this, ctx)
    elif isinstance(node, exp.Star):
        result = build_star(node, ctx)
    elif isinstance(node, exp.Null):
        result = build_literal_null(node, ctx)
    elif isinstance(node, exp.Ordered):
        result = build_field(node.this, ctx)
    elif isinstance(node, exp.Distinct):
        result = build_field(node.expressions[0], ctx)
    elif isinstance(node, (exp.Window,)):
        result = build_window(node, ctx)
    elif isinstance(node, exp.Select):
        from app.lineage_engine.subquery_builder import build_subquery_select
        result, _ = build_subquery_select(node, ctx)
    elif isinstance(node, exp.Union):
        from app.lineage_engine.subquery_builder import build_subquery_union
        result = build_subquery_union(node, ctx)
    else:
        raise UnsupportedExpression(f"不支持的表达式类型: {type(node).__name__}")

    if not isinstance(node, _leaf_types):
        ctx.cache[cache_key] = result
    return result


# ── 1. Alias: 同名跳过 ──

def build_alias(node: exp.Alias, ctx: BuildContext) -> str:
    inner_h = build_field(node.this, ctx)
    alias_name = node.alias

    # 如果 alias 名称和被包装字段的列名一致，跳过 alias 节点
    if isinstance(node.this, exp.Column) and node.this.name == alias_name:
        return inner_h

    expression = {"op": "alias", "args": [{"hash": inner_h}], "alias_name": alias_name}
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    ctx.upsert_field_reference(inner_h, h)
    return h


# ── 2. 聚合 groupby 包装 ──

def build_function(node: exp.Expression, ctx: BuildContext) -> str:
    """函数类表达式"""

    # 确定 op 名
    op = _function_op(node)

    # 提取子表达式
    child_hashes = _function_children(node, ctx)

    expression = {"op": op, "args": [{"hash": h} for h in child_hashes]}
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    for ch in child_hashes:
        ctx.upsert_field_reference(ch, h)

    # 聚合函数 → groupby 包装
    if op in ("sum", "count", "avg", "min", "max") and ctx.group_keys:
        h = _wrap_groupby(h, child_hashes, ctx)

    return h


def _function_op(node: exp.Expression) -> str:
    if isinstance(node, exp.Coalesce):     return "nvl"
    if isinstance(node, exp.Concat):       return "concat"
    if isinstance(node, exp.Substring):    return "substring"
    if isinstance(node, exp.Round):        return "round"
    if isinstance(node, exp.Count):        return "count"
    if isinstance(node, exp.Sum):          return "sum"
    if isinstance(node, exp.Avg):          return "avg"
    if isinstance(node, exp.Min):          return "min"
    if isinstance(node, exp.Max):          return "max"
    if isinstance(node, exp.RowNumber):    return "row_number"
    if isinstance(node, exp.Explode):      return "explode"
    if isinstance(node, exp.Split):        return "split"
    if hasattr(node, "name") and node.name: return node.name.lower()
    return type(node).__name__.lower()


def _function_children(node: exp.Expression, ctx: BuildContext) -> list[str]:
    children = []

    if isinstance(node, exp.Coalesce):
        children.append(build_field(node.this, ctx))
        for arg in (node.expressions or []):
            children.append(build_field(arg, ctx))
    elif isinstance(node, exp.Substring):
        children.append(build_field(node.this, ctx))
        for key in ("start", "length"):
            arg = node.args.get(key)
            if arg:
                children.append(build_field(arg, ctx))
    elif isinstance(node, exp.Concat):
        for arg in (node.expressions or []):
            children.append(build_field(arg, ctx))
    elif isinstance(node, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
        if node.args.get("this"):
            children.append(build_field(node.this, ctx))
        for arg in (node.expressions or []):
            children.append(build_field(arg, ctx))
    elif isinstance(node, exp.Round):
        children.append(build_field(node.this, ctx))
        if node.expression:
            children.append(build_field(node.expression, ctx))
    elif isinstance(node, (exp.RowNumber,)):
        # row_number() 无参数
        pass
    elif isinstance(node, (exp.Explode, exp.Split)):
        # explode / split 的子表达式
        if node.args.get("this"):
            children.append(build_field(node.this, ctx))
        for arg in (node.expressions or []):
            children.append(build_field(arg, ctx))
    elif hasattr(node, "expressions") and node.expressions:
        for arg in node.expressions:
            children.append(build_field(arg, ctx))
    elif hasattr(node, "this") and node.this:
        children.append(build_field(node.this, ctx))

    return children


def _wrap_groupby(agg_hash: str, agg_children: list[str], ctx: BuildContext) -> str:
    """将聚合函数包装为 groupby(agg, keys=[...])"""
    group_keys = ctx.group_keys
    expression = {
        "op": "groupby",
        "args": [{"hash": agg_hash}],
        "group_keys": [{"hash": h} for h in group_keys],
    }
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    ctx.upsert_field_reference(agg_hash, h)
    for gk in group_keys:
        ctx.upsert_field_reference(gk, h)
    return h


# ── 5. AND/OR 多元参数 ──

def build_expression(node: exp.Expression, ctx: BuildContext) -> str:
    """处理普通表达式（二元/一元运算、条件等）"""
    op = type(node).__name__.lower()

    child_hashes = []
    if isinstance(node, exp.Binary):
        left_h = build_field(node.this, ctx)
        right_h = build_field(node.expression, ctx)
        if op in ("add", "mul", "eq", "neq"):
            child_hashes = sorted([left_h, right_h])
        elif op in ("and", "or"):
            # 扁平化嵌套的 AND/OR
            child_hashes = _flatten_same_op(left_h, right_h, op, ctx)
            child_hashes = sorted(set(child_hashes))
        else:
            child_hashes = [left_h, right_h]
    elif isinstance(node, exp.Unary):
        child_h = build_field(node.this, ctx)
        child_hashes = [child_h]

    expression = {"op": op, "args": [{"hash": h} for h in child_hashes]}
    h = compute_hash(expression)

    condition_ops = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "notin", "like", "is", "between"}
    composite_ops = {"and", "or", "not"}

    field_type = (
        "composite_condition" if op in composite_ops else
        "atomic_condition" if op in condition_ops else
        "expression"
    )

    ctx.upsert_field(h, field_type, expression)
    for ch in child_hashes:
        ctx.upsert_field_reference(ch, h)
    return h


def _flatten_same_op(left_h: str, right_h: str, op: str, ctx: BuildContext) -> list[str]:
    """扁平化嵌套的 AND/OR：从 DB 读取已存在节点的子节点"""
    from app.database import get_cursor

    def _collect(hash_val: str) -> list[str]:
        with get_cursor() as cur:
            cur.execute("SELECT expression FROM fields WHERE hash = %s", (hash_val,))
            row = cur.fetchone()
        if not row:
            return [hash_val]
        expr = row["expression"]
        if isinstance(expr, str):
            import json; expr = json.loads(expr)
        if expr.get("op") == op:
            return [a["hash"] for a in expr.get("args", [])]
        return [hash_val]

    result = []
    for h in _collect(left_h):
        result.extend(_flatten_same_op(h, "", op, ctx) if False else [h])
    for h in _collect(right_h):
        result.append(h)
    return list(dict.fromkeys(result))


# ── 6. 窗口函数 (row_number op 名) ──

def build_window(node: exp.Window, ctx: BuildContext) -> str:
    inner = node.this
    inner_h = build_field(inner, ctx)

    partition_hashes = []
    for p in (node.args.get("partition_by") or []):
        partition_hashes.append(build_field(p, ctx))

    order_hashes = []
    order_clause = node.args.get("order")
    if order_clause:
        for o in (order_clause.expressions if hasattr(order_clause, "expressions") else []):
            order_hashes.append(build_field(o, ctx))

    frame = str(node.args.get("spec") or "")

    expression = {
        "op": "window",
        "args": [{"hash": inner_h}],
        "partition_by": [{"hash": h} for h in partition_hashes],
        "order_by": [{"hash": h} for h in order_hashes],
        "frame": frame,
    }
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    ctx.upsert_field_reference(inner_h, h)
    for ph in partition_hashes:
        ctx.upsert_field_reference(ph, h)
    for oh in order_hashes:
        ctx.upsert_field_reference(oh, h)
    return h


# ── 4. JOIN 包装 (INNER/RIGHT/FULL) ──

def build_alias_map(select_ast: exp.Select, ctx: BuildContext):
    """从 SELECT 的 FROM + JOIN 子句提取 alias → table_name 映射 + 子查询"""
    alias_map = ctx.alias_map
    join_on_map = ctx.join_on_map

    def _reg_table(node: exp.Table):
        name = node.name
        alias = node.alias
        if name:
            real_name = name if isinstance(name, str) else name.name
            if alias and alias in alias_map and alias_map[alias].startswith("__"):
                return real_name, alias
            if not alias and real_name in alias_map and alias_map[real_name].startswith("__"):
                return real_name, real_name
            if alias:
                alias_map[alias] = real_name
            else:
                alias_map[real_name] = real_name
            return real_name, (alias or real_name)

    def _reg_subquery(node, outer_alias):
        inner = node.this if isinstance(node, exp.Subquery) else node
        if outer_alias and isinstance(inner, exp.Select):
            from app.lineage_engine.subquery_builder import build_subquery_select
            inner_sq_hash, alias_to_hash = build_subquery_select(inner, ctx)
            alias_map[outer_alias] = f"__subquery__{inner_sq_hash}"
            ctx.subquery_map[outer_alias] = {"sq_hash": inner_sq_hash, "ast": inner, "alias_to_hash": alias_to_hash}
            return outer_alias

    def _extract_from(expr):
        if isinstance(expr, exp.Table):
            _reg_table(expr)
        elif isinstance(expr, (exp.Select, exp.Subquery)):
            _reg_subquery(expr, expr.alias)
        elif isinstance(expr, exp.Join):
            _extract_from(expr.this)
            _extract_from(expr.expression)

    # FROM
    from_expr = select_ast.args.get("from")
    if from_expr:
        _extract_from(from_expr.this)
        # LEFT table key
        left_key = None
        if isinstance(from_expr.this, exp.Table):
            result = _reg_table(from_expr.this)
            if result:
                left_key = result[1]

    # LATERAL VIEW
    for node in select_ast.walk():
        if isinstance(node, exp.Lateral):
            lateral_alias = node.alias
            if lateral_alias:
                alias_map[lateral_alias] = f"__lateral__{lateral_alias}"
                ctx.subquery_map[lateral_alias] = {"sq_hash": "__lateral__", "ast": node}

    # JOINs
    for i, join in enumerate(select_ast.args.get("joins") or []):
        join_table = join.this
        join_side = (join.args.get("side") or "").upper()
        join_kind = (join.args.get("kind") or "").upper()

        # RIGHT JOIN → 转化为 LEFT JOIN 语义（交换左右）
        if join_side == "RIGHT":
            join_side = "LEFT"
            # 标记为要交换 — 简化处理：对右表做 LEFT 包装
            # 实际上 RIGHT JOIN 应该交换 FROM 和 JOIN 的表，这里先做简化

        # FULL JOIN 在入参阶段应被拒绝（需要先转为 UNION）

        # 注册 JOIN 表别名
        right_key = None
        if isinstance(join_table, exp.Table):
            result = _reg_table(join_table)
            if result:
                right_key = result[1]
        elif isinstance(join_table, (exp.Select, exp.Subquery)):
            right_key = _reg_subquery(join_table, join_table.alias)

        # 构建 ON
        on_node = join.args.get("on")
        on_hash = None
        if on_node:
            on_hash = build_field(on_node, ctx)

        if on_hash:
            # INNER JOIN: 左右表都包裹
            if join_kind == "INNER" or join_side == "INNER":
                if left_key:
                    join_on_map[left_key] = on_hash
                if right_key:
                    join_on_map[right_key] = on_hash
            # LEFT JOIN: 只包裹右表
            elif join_side == "LEFT" or not join_side:
                if right_key:
                    join_on_map[right_key] = on_hash


# ── 基础函数 ──

def build_literal(node: exp.Literal, ctx: BuildContext) -> str:
    value = node.this
    type_name = "string" if node.is_string else "number"
    expression = {"op": "literal", "value": str(value), "type": type_name}
    h = compute_hash(expression)
    ctx.upsert_field(h, "literal", expression)
    return h


def build_column(node: exp.Column, ctx: BuildContext) -> str:
    col_name = node.name
    table_ident = node.table
    if not table_ident:
        raise UnsupportedExpression(f"无法解析列 {col_name} 的来源表")

    table_name = table_ident if isinstance(table_ident, str) else table_ident.name
    original_alias = table_name
    while table_name in ctx.alias_map:
        resolved = ctx.alias_map[table_name]
        if resolved.startswith("__subquery__"):
            inner_hash = _resolve_subquery_column(table_name, col_name, ctx)
            return _maybe_wrap_join(inner_hash, table_name, ctx)
        if resolved.startswith("__lateral__"):
            return _resolve_lateral_column(table_name, col_name, ctx)
        if resolved == table_name:
            break
        table_name = resolved

    db_ident = node.args.get("db")
    schema_name = ""
    if db_ident:
        schema_name = db_ident if isinstance(db_ident, str) else db_ident.name
    full_table = f"{schema_name}.{table_name}" if schema_name else table_name

    table_rec = ctx.get_table(schema_name or "", table_name)
    if not table_rec:
        raise MissingLineageData(f"表 {full_table} 不存在")

    base_hash = None
    if table_rec["source"] == "root":
        expression = {"op": "column", "table": full_table, "column": col_name}
        base_hash = compute_hash(expression)
        ctx.upsert_field(base_hash, "atomic", expression)
    elif table_rec["source"] == "middle":
        tc = ctx.get_table_column_by_name(table_rec["id"], col_name)
        if not tc or not tc["field_hash"]:
            raise MissingLineageData(f"middle 表 {full_table}.{col_name} 缺少血缘数据")
        base_hash = tc["field_hash"]
    else:
        raise MissingLineageData(f"未知的表来源: {table_rec['source']}")

    return _maybe_wrap_join(base_hash, original_alias, ctx)


def build_case_when(node: exp.Case, ctx: BuildContext) -> str:
    # 按原始顺序构建 WHEN 子句，ELSE 放在最后
    args = []
    ifs = node.args.get("ifs") or []
    for item in ifs:
        cond_h = build_field(item.this, ctx)
        val_h = build_field(item.args["true"], ctx)
        args.append({"condition": cond_h, "value": val_h})
    default_val = node.args.get("default")
    if default_val:
        default_h = build_field(default_val, ctx)
        args.append({"condition": None, "value": default_h})
    expression = {"op": "casewhen", "args": args}
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    for arg in args:
        if arg["condition"]:
            ctx.upsert_field_reference(arg["condition"], h)
        ctx.upsert_field_reference(arg["value"], h)
    return h


def build_cast(node: exp.Cast, ctx: BuildContext) -> str:
    inner_h = build_field(node.this, ctx)
    to_type = node.to.sql() if hasattr(node.to, "sql") else str(node.to)
    expression = {"op": "cast", "args": [{"hash": inner_h}], "to_type": to_type}
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    ctx.upsert_field_reference(inner_h, h)
    return h


def build_star(node: exp.Star, ctx: BuildContext) -> str:
    expression = {"op": "star"}
    h = compute_hash(expression)
    ctx.upsert_field(h, "literal", expression)
    return h


def build_literal_null(node: exp.Null, ctx: BuildContext) -> str:
    expression = {"op": "literal", "value": "NULL", "type": "null"}
    h = compute_hash(expression)
    ctx.upsert_field(h, "literal", expression)
    return h


def build_in(node: exp.In, ctx: BuildContext) -> str:
    left_h = build_field(node.this, ctx)
    child_hashes = [left_h]
    for arg in (node.expressions or []):
        child_hashes.append(build_field(arg, ctx))
    op = "notin" if node.args.get("is_global") else "in"
    expression = {"op": op, "args": [{"hash": h} for h in child_hashes]}
    h = compute_hash(expression)
    ctx.upsert_field(h, "atomic_condition", expression)
    for ch in child_hashes:
        ctx.upsert_field_reference(ch, h)
    return h


def _maybe_wrap_join(inner_hash: str, alias: str, ctx: BuildContext) -> str:
    """如果 alias 在 join_on_map 中，包装为 JOIN 表达式"""
    on_hash = ctx.join_on_map.get(alias)
    if not on_hash or not inner_hash:
        return inner_hash
    join_expr = {"op": "JOIN", "args": [{"hash": inner_hash}], "on": on_hash}
    join_h = compute_hash(join_expr)
    ctx.upsert_field(join_h, "expression", join_expr)
    ctx.upsert_field_reference(inner_hash, join_h)
    ctx.upsert_field_reference(on_hash, join_h)
    return join_h


def _resolve_subquery_column(alias: str, col_name: str, ctx: BuildContext) -> str:
    sub_info = ctx.subquery_map.get(alias)
    if not sub_info:
        raise MissingLineageData(f"子查询别名 {alias} 未找到")

    # 优先使用 alias_to_hash（子查询构建时记录的 alias→hash 映射）
    alias_to_hash = sub_info.get("alias_to_hash", {})
    if col_name in alias_to_hash:
        return alias_to_hash[col_name]

    # 兜底：从 AST 中查找
    sq_ast = sub_info.get("ast")
    if sq_ast:
        for expr in sq_ast.expressions:
            if isinstance(expr, exp.Alias):
                if expr.alias == col_name:
                    return build_field(expr.this, ctx)
            elif isinstance(expr, exp.Column):
                if expr.name == col_name:
                    return build_field(expr, ctx)

    raise MissingLineageData(f"子查询 {alias} 中未找到列 {col_name}")


def _resolve_lateral_column(alias: str, col_name: str, ctx: BuildContext) -> str:
    sub_info = ctx.subquery_map.get(alias)
    if not sub_info:
        raise MissingLineageData(f"LATERAL VIEW 别名 {alias} 未找到")
    lateral_node = sub_info["ast"]
    inner_h = build_field(lateral_node.this, ctx)
    expression = {"op": "JOIN", "args": [{"hash": inner_h}], "on": None, "lateral": True}
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    ctx.upsert_field_reference(inner_h, h)
    return h
