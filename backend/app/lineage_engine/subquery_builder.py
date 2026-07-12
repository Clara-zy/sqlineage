"""Phase 4 子查询构建"""

import sqlglot.expressions as exp

from app.lineage_engine.context import BuildContext, compute_hash
from app.lineage_engine.field_builder import build_field


def build_subquery_select(node: exp.Select, ctx: BuildContext) -> tuple[str, dict]:
    """构建 SELECT 子查询节点 → (hash, alias_to_hash)"""
    from app.lineage_engine.field_builder import build_alias_map
    build_alias_map(node, ctx)

    # 先构建 GROUP BY 字段 hash（聚合函数包装时需要）
    saved_group_keys = ctx.group_keys
    ctx.group_keys = []
    group_expr = node.args.get("group")
    if group_expr:
        for key_expr in (group_expr.expressions if hasattr(group_expr, "expressions") else []):
            ctx.group_keys.append(build_field(key_expr, ctx))

    # 遍历 projection 构建字段 hash，同时记录 alias→hash 映射
    field_hashes = []
    alias_to_hash = {}
    for expr in node.expressions:
        h = build_field(expr, ctx)
        field_hashes.append(h)
        if isinstance(expr, exp.Alias):
            alias_to_hash[expr.alias] = h
        elif isinstance(expr, exp.Column):
            alias_to_hash[expr.name] = h

    # 恢复 group_keys
    ctx.group_keys = saved_group_keys

    # 构建 where 条件
    where_hash = None
    if node.args.get("where"):
        where_hash = build_field(node.args["where"], ctx)

    structure = {"type": "select", "fields": field_hashes, "alias_map": alias_to_hash}
    if where_hash:
        structure["where"] = where_hash

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
