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

    # 5. HAVING 包裹每个投影列
    current_having = ctx.pending_having
    if current_having:
        from app.field_enums import inherit_enum
        wrapped = []
        for fh in field_hashes:
            having_expr = {"op": "having", "args": [{"hash": fh}], "condition": current_having}
            wh = compute_hash(having_expr)
            ctx.upsert_field(wh, "expression", having_expr)
            ctx.upsert_field_reference(fh, wh)
            ctx.upsert_field_reference(current_having, wh)
            inherit_enum(fh, wh)
            wrapped.append(wh)
        field_hashes = wrapped

    # 6. ORDER BY — sort key 作为 field，hash 存入 order_keys（供 LIMIT 引用）
    order_keys = []
    order_expr = node.args.get("order")
    if order_expr:
        for o in (order_expr.expressions if hasattr(order_expr, "expressions") else []):
            key_h = build_field(o, ctx)
            desc = bool(o.args.get("desc"))
            order_keys.append({"hash": key_h, "desc": desc})

    # 7. LIMIT 包裹每个投影列，ORDER BY 作为 limt 的可选参数
    limit_val = None
    limit_node = node.args.get("limit")
    if limit_node or order_keys:
        limit_val = int(limit_node.expression.this) if (limit_node and limit_node.expression) else None
        from app.field_enums import inherit_enum
        wrapped = []
        for fh in field_hashes:
            lim_expr = {"op": "limt", "args": [{"hash": fh}]}
            if limit_val is not None:
                lim_expr["limit"] = limit_val
            if order_keys:
                lim_expr["order_by"] = [{"hash": ok["hash"], "desc": ok["desc"]} for ok in order_keys]
            lh = compute_hash(lim_expr)
            ctx.upsert_field(lh, "expression", lim_expr)
            ctx.upsert_field_reference(fh, lh)
            for ok in order_keys:
                ctx.upsert_field_reference(ok["hash"], lh)
            inherit_enum(fh, lh)
            wrapped.append(lh)
        field_hashes = wrapped

    inner_sqs = list(ctx.inner_subqueries)
    ctx.inner_subqueries.clear()

    # 恢复
    ctx.group_keys = saved_group_keys
    ctx.pending_where = saved_where
    ctx.pending_having = saved_having

    structure = {"type": "select", "fields": field_hashes, "alias_map": alias_to_hash}
    if order_keys:
        structure["order_by"] = order_keys
    if limit_val is not None:
        structure["limit"] = limit_val
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



