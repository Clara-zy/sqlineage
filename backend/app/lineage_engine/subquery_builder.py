"""Phase 4 子查询构建"""

import sqlglot.expressions as exp

from app.lineage_engine.context import BuildContext, compute_hash
from app.lineage_engine.field_builder import build_field


def build_subquery_select(node: exp.Select, ctx: BuildContext) -> tuple[str, dict]:
    """构建 SELECT 子查询节点 → (hash, alias_to_hash)

    WHERE 在列引用层应用（_maybe_wrap_join），位于 JOIN 之后、聚合之前。
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

    # 3. 构建投影列（列引用层已带 WHERE + JOIN）
    field_hashes = []
    alias_to_hash = {}
    for expr in node.expressions:
        h = build_field(expr, ctx)
        field_hashes.append(h)
        if isinstance(expr, exp.Alias):
            alias_to_hash[expr.alias] = h
        elif isinstance(expr, exp.Column):
            alias_to_hash[expr.name] = h

    # 恢复
    ctx.group_keys = saved_group_keys
    ctx.pending_where = saved_where

    structure = {"type": "select", "fields": field_hashes, "alias_map": alias_to_hash}

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
