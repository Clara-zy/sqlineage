"""Phase 4 子查询构建"""

import sqlglot.expressions as exp

from app.lineage_engine.context import BuildContext, compute_hash
from app.lineage_engine.field_builder import build_field


def build_subquery_select(node: exp.Select, ctx: BuildContext) -> tuple[str, dict]:
    """构建 SELECT 子查询节点 → (hash, alias_to_hash)

    WHERE 在列引用层应用（_maybe_wrap_join），位于 JOIN 之后、聚合之前。
    HAVING 在聚合后，作为子查询级过滤存入 structure。
    """
    from app.lineage_engine.field_builder import build_alias_map

    # 词法作用域：快照别名相关的上下文，退出时恢复
    # （多层嵌套里 a/b/x 这类短别名会被反复复用，全局表会互相覆盖）
    saved_alias_map = dict(ctx.alias_map)
    saved_subquery_map = dict(ctx.subquery_map)
    saved_join_wraps = {k: list(v) for k, v in ctx.join_wraps.items()}
    saved_from_alias = ctx.from_alias
    build_alias_map(node, ctx)

    # 1. 构建 WHERE，存入 ctx.pending_where（列引用时自动应用）
    saved_where = ctx.pending_where
    ctx.pending_where = None
    if node.args.get("where"):
        ctx.pending_where = build_field(node.args["where"], ctx)

    # 2. 构建 GROUP BY 字段 hash（列引用层已带 WHERE）
    saved_group_keys = ctx.group_keys
    ctx.group_keys = []
    group_expr = node.args.get("group")
    if group_expr:
        for key_expr in (group_expr.expressions if hasattr(group_expr, "expressions") else []):
            ctx.group_keys.append(build_field(key_expr, ctx))

    # 3. 构建 HAVING — 存入 ctx.pending_having（投影列构建时统一包裹）
    saved_having = ctx.pending_having
    ctx.pending_having = None
    if node.args.get("having"):
        ctx.pending_having = build_field(node.args["having"], ctx)

    # 4. 构建投影列（WHERE/JOIN 在列引用层，HAVING 在投影层统一包裹）
    field_hashes = []
    alias_to_hash = {}
    for expr in node.expressions:
        h = build_field(expr, ctx)
        field_hashes.append(h)
        if isinstance(expr, exp.Alias):
            alias_to_hash[expr.alias] = h
        elif isinstance(expr, exp.Column):
            alias_to_hash[expr.name] = h

    # 5. 记录投影别名 → hash（ORDER BY 中引用 SELECT 别名时需要）
    saved_proj = ctx.projection_map
    ctx.projection_map = dict(alias_to_hash)

    # 6. HAVING 包裹每个投影列 → 去 having 后统一为 where（组过滤，时机由 DAG 位置承载）
    current_having = ctx.pending_having
    if current_having:
        from app.field_enums import inherit_enum
        wrapped = []
        for fh in field_hashes:
            where_expr = {"op": "where", "args": [{"hash": fh}], "condition": current_having}
            wh = compute_hash(where_expr)
            ctx.upsert_field(wh, "expression", where_expr)
            ctx.upsert_field_reference(fh, wh)
            ctx.upsert_field_reference(current_having, wh)
            inherit_enum(fh, wh)
            wrapped.append(wh)
        field_hashes = wrapped

    # 6. ORDER BY — 独立 ordr 节点包裹 sort key，供 LIMIT 引用
    order_hashes = []
    order_expr = node.args.get("order")
    if order_expr:
        for o in (order_expr.expressions if hasattr(order_expr, "expressions") else []):
            key_h = build_field(o, ctx)
            desc = bool(o.args.get("desc"))
            ordr_expr = {"op": "ordr", "args": [{"hash": key_h}], "desc": desc}
            oh = compute_hash(ordr_expr)
            ctx.upsert_field(oh, "expression", ordr_expr)
            ctx.upsert_field_reference(key_h, oh)
            order_hashes.append(oh)

    # 7. LIMIT 包裹投影列，引用 ORDER BY 的 ordr 节点
    limit_val = None
    limit_node = node.args.get("limit")
    if limit_node:
        limit_val = int(limit_node.expression.this) if limit_node.expression else 0
    if limit_node or order_hashes:
        from app.field_enums import inherit_enum
        wrapped = []
        for fh in field_hashes:
            lim_expr = {"op": "limt", "args": [{"hash": fh}]}
            if limit_val is not None and limit_node:
                lim_expr["limit"] = limit_val
            if order_hashes:
                lim_expr["order_by"] = [oh for oh in order_hashes]
            lh = compute_hash(lim_expr)
            ctx.upsert_field(lh, "expression", lim_expr)
            ctx.upsert_field_reference(fh, lh)
            for oh in order_hashes:
                ctx.upsert_field_reference(oh, lh)
            inherit_enum(fh, lh)
            wrapped.append(lh)
        field_hashes = wrapped

    inner_sqs = list(ctx.inner_subqueries)
    ctx.inner_subqueries.clear()

    # 恢复（原地恢复，保持 dict 对象身份：调用方可能持有 ctx.alias_map 引用）
    ctx.group_keys = saved_group_keys
    ctx.pending_where = saved_where
    ctx.pending_having = saved_having
    ctx.projection_map = saved_proj
    ctx.alias_map.clear()
    ctx.alias_map.update(saved_alias_map)
    ctx.subquery_map.clear()
    ctx.subquery_map.update(saved_subquery_map)
    ctx.join_wraps.clear()
    ctx.join_wraps.update(saved_join_wraps)
    ctx.from_alias = saved_from_alias

    structure = {"type": "select", "fields": field_hashes, "alias_map": alias_to_hash}
    if inner_sqs:
        structure["inner_subqueries"] = inner_sqs

    h = compute_hash(structure)
    ctx.upsert_subquery(h, "select", structure)
    return h, alias_to_hash


def build_subquery_union(node: exp.Union, ctx: BuildContext) -> str:
    """构建 UNION 子查询节点"""
    branch_hashes = []

    # this = 左支, expression = 右支, expressions = 额外分支
    branches = [node.this]
    if node.expression:
        branches.append(node.expression)
    for b in (node.expressions or []):
        branches.append(b)

    for branch in branches:
        branch_h = build_field(branch, ctx)
        branch_hashes.append(branch_h)

    distinct = node.args.get("distinct", False)
    structure = {"type": "union", "branches": branch_hashes, "distinct": distinct}
    h = compute_hash(structure)
    ctx.upsert_subquery(h, "union", structure)
    return h



