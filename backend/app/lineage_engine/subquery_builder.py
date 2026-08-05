"""Phase 4 子查询构建"""

import sqlglot.expressions as exp

from app.lineage_engine.context import BuildContext, compute_hash
from app.lineage_engine.field_builder import build_field
from app.database import get_cursor
import json


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

    # 3. 构建 HAVING — 存入 ctx.pending_having，并显式创建 having 包裹节点
    saved_having = ctx.pending_having
    ctx.pending_having = None
    if node.args.get("having"):
        having_cond = build_field(node.args["having"], ctx)
        ctx.pending_having = having_cond
        # 显式创建 having 节点包裹 HAVING 条件引用的 groupby 聚合
        _wrap_having_on_groupbys(having_cond, ctx)

    # 4. 构建投影列（WHERE/JOIN/HAVING 均已在对应层级自动应用）
    field_hashes = []
    alias_to_hash = {}
    for expr in node.expressions:
        h = build_field(expr, ctx)
        field_hashes.append(h)
        if isinstance(expr, exp.Alias):
            alias_to_hash[expr.alias] = h
        elif isinstance(expr, exp.Column):
            alias_to_hash[expr.name] = h

    # 捕获当前 HAVING + 内嵌子查询（恢复前）
    current_having = ctx.pending_having
    inner_sqs = list(ctx.inner_subqueries)
    ctx.inner_subqueries.clear()

    # 恢复
    ctx.group_keys = saved_group_keys
    ctx.pending_where = saved_where
    ctx.pending_having = saved_having

    # HAVING 条件 hash 加入 fields，使其作为 field 节点通过 DAG 可达
    if current_having:
        field_hashes.append(current_having)

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


def _wrap_having_on_groupbys(having_cond: str, ctx: BuildContext):
    """遍历 HAVING 条件引用的 groupby 节点，为每个创建 having 包裹。
    HAVING 条件内部聚合已 groupby-wrapped（ctx.group_keys 已设置），
    此处在其上再包一层 having 节点指向 HAVING 条件。
    """
    from app.field_enums import inherit_enum

    visited: set[str] = set()
    queue = [having_cond]

    while queue:
        fh = queue.pop(0)
        if fh in visited:
            continue
        visited.add(fh)

        with get_cursor() as cur:
            cur.execute("SELECT expression FROM fields WHERE hash = %s", (fh,))
            row = cur.fetchone()
            if not row:
                continue
            expr = row["expression"]
            if isinstance(expr, str):
                expr = json.loads(expr)

            # 收集子引用
            for a in expr.get("args", []):
                sub_h = a.get("hash", "")
                if sub_h and sub_h not in visited:
                    queue.append(sub_h)

        # 对 groupby 节点创建 having 包裹
        if expr.get("op") == "groupby":
            having_expr = {"op": "having", "args": [{"hash": fh}], "condition": having_cond}
            new_h = compute_hash(having_expr)
            ctx.upsert_field(new_h, "expression", having_expr)
            with get_cursor() as cur:
                cur.execute(
                    "INSERT INTO field_references (referenced_hash, referrer_hash) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (fh, new_h),
                )
                cur.execute(
                    "INSERT INTO field_references (referenced_hash, referrer_hash) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (having_cond, new_h),
                )
            inherit_enum(fh, new_h)
