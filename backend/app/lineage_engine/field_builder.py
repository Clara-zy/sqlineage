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
    elif isinstance(node, (exp.Where, exp.Having)):
        result = build_field(node.this, ctx)
    elif isinstance(node, exp.Star):
        result = build_star(node, ctx)
    elif isinstance(node, exp.Null):
        result = build_literal_null(node, ctx)
    elif isinstance(node, exp.Subquery):
        result = build_subquery(node, ctx)
    elif isinstance(node, exp.CurrentTimestamp):
        result = build_current_timestamp(ctx)
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
    # 枚举继承
    from app.field_enums import inherit_enum
    inherit_enum(inner_h, h)
    return h


# ── 2. 聚合 groupby 包装 ──

def build_function(node: exp.Expression, ctx: BuildContext) -> str:
    """函数类表达式 — 通用 args 遍历，位置参数 + 命名参数"""
    op = _function_op(node)
    pos_hashes, named_params = _extract_args(node, ctx)

    expression = {"op": op, "args": [{"hash": h} for h in pos_hashes]}
    if named_params:
        expression["params"] = named_params

    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    for ch in pos_hashes:
        ctx.upsert_field_reference(ch, h)
    for v in named_params.values():
        if isinstance(v, str) and len(v) == 16:  # hash reference
            ctx.upsert_field_reference(v, h)

    # 聚合函数 → groupby 包装
    if op in ("sum", "count", "avg", "min", "max") and ctx.group_keys:
        h = _wrap_groupby(h, pos_hashes, ctx)

    return h


def _function_op(node: exp.Expression) -> str:
    # 优先按类型匹配（避免 Cast 等有 name 属性但语义不符的节点走错）
    type_name = type(node).__name__.lower()
    _type_ops = {
        "coalesce": "nvl", "concat": "concat", "substring": "substring",
        "round": "round", "cast": "cast", "if": "if",
        "count": "count", "sum": "sum", "avg": "avg", "min": "min", "max": "max",
        "rownumber": "row_number", "explode": "explode", "split": "split",
    }
    if type_name in _type_ops:
        return _type_ops[type_name]
    if hasattr(node, "name") and node.name and isinstance(node.name, str):
        return node.name.lower()
    return type_name


def _extract_args(node: exp.Expression, ctx: BuildContext) -> tuple[list[str], dict[str, str]]:
    """通用参数提取：位置参数入 list，命名参数入 dict。
    遍历 node.args 中所有 Expression 类型的值，跳过元数据 key。
    """
    pos_hashes: list[str] = []
    named: dict[str, str] = {}

    # 元数据 / 内部标记 key，不参与血缘
    SKIP_KEYS = {"distinct", "spec", "default", "is_global", "safe", "coalesce", "action", "format"}

    done_keys = set()

    # this 总是第一个位置参数
    this = node.args.get("this")
    if this is not None and isinstance(this, exp.Expression):
        pos_hashes.append(build_field(this, ctx))
        done_keys.add("this")

    # expression 作为第二个位置参数
    expr = node.args.get("expression")
    if expr is not None and isinstance(expr, exp.Expression):
        pos_hashes.append(build_field(expr, ctx))
        done_keys.add("expression")

    # expressions 列表作为后续位置参数
    for e in (node.args.get("expressions") or []):
        if isinstance(e, exp.Expression):
            pos_hashes.append(build_field(e, ctx))
    done_keys.add("expressions")

    # 其余 args 中的 Expression → named params；字符串 → 原样保留
    for key, val in node.args.items():
        if key in done_keys or key in SKIP_KEYS:
            continue
        if isinstance(val, exp.DataType):
            named[key] = val.sql()  # cast 的 to 类型名
        elif isinstance(val, exp.Expression):
            named[key] = build_field(val, ctx)
        elif isinstance(val, str) and val:
            named[key] = val
        elif isinstance(val, (int, float)):
            named[key] = str(val)

    return pos_hashes, named


def _wrap_groupby(agg_hash: str, agg_children: list[str], ctx: BuildContext) -> str:
    """将聚合函数包装为 groupby(agg, keys=[...])，再叠加 HAVING"""
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

    # HAVING 在 groupby 之后包裹
    if ctx.pending_having:
        having_expr = {"op": "having", "args": [{"hash": h}], "condition": ctx.pending_having}
        new_h = compute_hash(having_expr)
        ctx.upsert_field(new_h, "expression", having_expr)
        ctx.upsert_field_reference(h, new_h)
        ctx.upsert_field_reference(ctx.pending_having, new_h)
        from app.field_enums import inherit_enum
        inherit_enum(h, new_h)
        h = new_h

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

    # 枚举校验
    if op in ("eq", "neq", "in", "notin") and len(child_hashes) >= 2:
        _validate_enum_in_condition(child_hashes, op)

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
    """从 SELECT 的 FROM + JOIN 子句提取 alias → table_name 映射 + 子查询

    JOIN 包装采用累积模型：
      - join_wraps[alias] = [on_hash_1, on_hash_2, ...]  按 JOIN 顺序的 ON 栈
      - left_aliases: 当前「左侧」所有别名（INNER 合并，LEFT 不合并）
      - all_aliases: 所有已出现别名（用于 INNER 时全体包裹）
    """
    alias_map = ctx.alias_map
    join_wraps = ctx.join_wraps

    left_aliases: set[str] = set()
    all_aliases: set[str] = set()

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

    # ── FROM ──
    from_expr = select_ast.args.get("from")
    if from_expr:
        from_src = from_expr.this
        # 注册
        if isinstance(from_src, exp.Table):
            result = _reg_table(from_src)
            if result:
                key = result[1]
                left_aliases.add(key)
                all_aliases.add(key)
                join_wraps.setdefault(key, [])
        elif isinstance(from_src, (exp.Select, exp.Subquery)):
            key = _reg_subquery(from_src, from_src.alias)
            if key:
                left_aliases.add(key)
                all_aliases.add(key)
                join_wraps.setdefault(key, [])

    # LATERAL VIEW
    for node in select_ast.walk():
        if isinstance(node, exp.Lateral):
            lateral_alias = node.alias
            if lateral_alias:
                alias_map[lateral_alias] = f"__lateral__{lateral_alias}"
                ctx.subquery_map[lateral_alias] = {"sq_hash": "__lateral__", "ast": node}

    # ── JOINs ──
    for join in (select_ast.args.get("joins") or []):
        join_table = join.this
        join_kind = (join.args.get("kind") or "").upper()
        join_side = (join.args.get("side") or "").upper()

        # 注册右表
        right_key = None
        if isinstance(join_table, exp.Table):
            result = _reg_table(join_table)
            if result:
                right_key = result[1]
        elif isinstance(join_table, (exp.Select, exp.Subquery)):
            right_key = _reg_subquery(join_table, join_table.alias)

        if not right_key:
            continue

        # 构建 ON
        on_node = join.args.get("on")
        on_hash = build_field(on_node, ctx) if on_node else None

        if not on_hash:
            all_aliases.add(right_key)
            join_wraps.setdefault(right_key, [])
            left_aliases.add(right_key)  # 无 ON 视为 CROSS JOIN，归入左侧
            continue

        if join_kind == "INNER":
            # INNER: 全体已有列 + 右表列都包当前 ON
            for alias in all_aliases:
                join_wraps.setdefault(alias, []).append(on_hash)
            join_wraps.setdefault(right_key, []).append(on_hash)
            left_aliases.add(right_key)
            all_aliases.add(right_key)
        else:
            # LEFT (默认): 仅右表包裹，左侧不变
            join_wraps.setdefault(right_key, []).append(on_hash)
            all_aliases.add(right_key)
            # left_aliases 不变


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
    db_name = ""
    if db_ident:
        db_name = db_ident if isinstance(db_ident, str) else db_ident.name
    full_table = f"{db_name}.{table_name}" if db_name else table_name

    table_rec = ctx.get_table(db_name or "", table_name)
    if not table_rec:
        raise MissingLineageData(f"表 {full_table} 不存在")

    base_hash = None
    if table_rec["source"] == "root":
        expression = {"op": "column", "table": full_table, "column": col_name}
        base_hash = compute_hash(expression)
        ctx.upsert_field(base_hash, "atomic", expression)
        # 枚举值：从 column_comment 解析 ENUM(...)
        _maybe_parse_enum(table_rec["id"], col_name, base_hash)
        # 回填 table_columns.field_hash
        ctx.update_table_column_field_hash(table_rec["id"], col_name, base_hash)
    elif table_rec["source"] == "sink":
        tc = ctx.get_table_column_by_name(table_rec["id"], col_name)
        if not tc or not tc["field_hash"]:
            raise MissingLineageData(f"sink 表 {full_table}.{col_name} 缺少血缘数据")
        base_hash = tc["field_hash"]
    elif table_rec["source"] == "view":
        base_hash = _resolve_view_column(table_rec, col_name, full_table)
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
    """CAST 委托给通用 build_function（to 类型名由 _extract_args 处理）"""
    return build_function(node, ctx)


def build_star(node: exp.Star, ctx: BuildContext) -> str:
    expression = {"op": "star"}
    h = compute_hash(expression)
    ctx.upsert_field(h, "literal", expression)
    return h


def build_subquery(node: exp.Subquery, ctx: BuildContext) -> str:
    """处理表达式中的子查询 (如 WHERE x IN (SELECT ...))
    创建包装 field 节点表示子查询结果，避免 field_references FK 约束冲突。
    """
    from app.lineage_engine.subquery_builder import build_subquery_select
    inner = node.this
    if isinstance(inner, exp.Select):
        sq_hash, _ = build_subquery_select(inner, ctx)
        expression = {"op": "subquery", "subquery_hash": sq_hash}
        h = compute_hash(expression)
        ctx.upsert_field(h, "expression", expression)
        return h
    raise UnsupportedExpression(f"不支持的子查询类型: {type(inner).__name__}")


def build_current_timestamp(ctx: BuildContext) -> str:
    """CURRENT_TIMESTAMP → 无参数内容寻址节点，全局复用"""
    expression = {"op": "current_timestamp"}
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
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
    """按 join_wraps[alias] 栈逐层包裹 JOIN，最后包 WHERE（列引用层）"""
    on_hashes = ctx.join_wraps.get(alias, [])
    from app.field_enums import inherit_enum

    result = inner_hash
    for on_hash in on_hashes:
        if not on_hash:
            continue
        join_expr = {"op": "join", "args": [{"hash": result}], "on": on_hash}
        new_h = compute_hash(join_expr)
        ctx.upsert_field(new_h, "expression", join_expr)
        ctx.upsert_field_reference(result, new_h)
        ctx.upsert_field_reference(on_hash, new_h)
        inherit_enum(result, new_h)
        result = new_h

    # WHERE 在 JOIN 之后、聚合之前
    if ctx.pending_where and result:
        where_expr = {"op": "where", "args": [{"hash": result}], "condition": ctx.pending_where}
        new_h = compute_hash(where_expr)
        ctx.upsert_field(new_h, "expression", where_expr)
        ctx.upsert_field_reference(result, new_h)
        ctx.upsert_field_reference(ctx.pending_where, new_h)
        inherit_enum(result, new_h)
        result = new_h

    return result


def _validate_enum_in_condition(child_hashes: list[str], op: str):
    """校验 eq/neq/in/notin 中枚举字段与字面量的匹配"""
    from app.database import get_cursor
    from app.field_enums import get_active_enum

    left_h = child_hashes[0]  # 字段/表达式
    right_hashes = child_hashes[1:]  # 字面量

    enum_values = get_active_enum(left_h)
    if not enum_values:
        return

    with get_cursor() as cur:
        for rh in right_hashes:
            cur.execute(
                "SELECT expression FROM fields WHERE hash = %s AND field_type = 'literal'",
                (rh,),
            )
            row = cur.fetchone()
            if not row:
                continue
            expr = row["expression"]
            lit_val = str(expr.get("value", ""))
            if lit_val and lit_val not in enum_values:
                print(
                    f"[ENUM-WARN] 字段 {left_h[:8]} 枚举值 {enum_values} "
                    f"不包含字面量 '{lit_val}' (op={op})"
                )


def _maybe_parse_enum(table_id: int, column_name: str, field_hash: str):
    """从 table_columns.comment 解析 ENUM(...) 并写入 field_enums"""
    from app.database import get_cursor
    from app.field_enums import parse_enum_from_comment, upsert_field_enum
    with get_cursor() as cur:
        cur.execute(
            "SELECT column_comment FROM table_columns "
            "WHERE table_id = %s AND column_name = %s",
            (table_id, column_name),
        )
        row = cur.fetchone()
    if row and row["column_comment"]:
        values = parse_enum_from_comment(row["column_comment"])
        if values:
            upsert_field_enum(field_hash, values, "ddl_comment")


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
    expression = {"op": "join", "args": [{"hash": inner_h}], "on": None, "lateral": True}
    h = compute_hash(expression)
    ctx.upsert_field(h, "expression", expression)
    ctx.upsert_field_reference(inner_h, h)
    return h


def _resolve_view_column(table_rec: dict, col_name: str, full_table: str) -> str:
    from app.database import get_cursor
    sq_hash = table_rec.get("input_subquery_hash")
    if not sq_hash:
        raise MissingLineageData(f"VIEW {full_table} 缺少子查询定义")
    with get_cursor() as cur:
        cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (sq_hash,))
        row = cur.fetchone()
        if not row:
            raise MissingLineageData(f"VIEW {full_table} 子查询不存在")
        structure = row["structure"]
        # 优先用 alias_map
        alias_map = structure.get("alias_map", {})
        if col_name in alias_map:
            return alias_map[col_name]
        # 兜底：遍历 fields
        for fh in structure.get("fields", []):
            cur.execute("SELECT expression FROM fields WHERE hash = %s", (fh,))
            fr = cur.fetchone()
            if fr:
                expr = fr["expression"]
                if (expr.get("op") == "alias" and expr.get("alias_name") == col_name) or \
                   (expr.get("op") == "column" and expr.get("column") == col_name):
                    return fh
    raise MissingLineageData(f"VIEW {full_table} 中未找到列 {col_name}")
