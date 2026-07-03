"""Phase 2 AST 标准化 — 基于 sqlglot optimizer"""

import sqlglot
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.normalize import normalize as norm_pass
from sqlglot.optimizer.simplify import simplify


def build_schema_dict() -> dict:
    """从 tables + table_columns 构建 sqlglot 的 schema dict"""
    from app.database import get_cursor

    schema = {}
    with get_cursor() as cur:
        cur.execute("SELECT id, schema_name, table_name FROM tables")
        tables = cur.fetchall()
        for t in tables:
            key = t["table_name"]
            cur.execute(
                "SELECT column_name, column_type FROM table_columns WHERE table_id = %s",
                (t["id"],),
            )
            columns = {r["column_name"]: r["column_type"] for r in cur.fetchall()}
            schema[key] = columns
    return schema


def normalize(sql: str, schema: dict) -> sqlglot.Expression:
    """对 SQL 做标准化：RIGHT/FULL JOIN → LEFT/UNION / qualify → dnf → simplify"""
    ast = sqlglot.parse_one(sql)

    # RIGHT JOIN → LEFT JOIN（交换 FROM 和 JOIN 表）
    ast = _transform_right_joins(ast)
    # FULL JOIN → (LEFT JOIN) UNION (LEFT JOIN)
    ast = _transform_full_joins(ast)

    if schema:
        ast = qualify(ast, schema=schema, dialect="mysql")
    ast = norm_pass(ast, dnf=True)
    ast = simplify(ast)

    return ast


def _transform_right_joins(ast: sqlglot.Expression) -> sqlglot.Expression:
    """将 RIGHT JOIN 转换为 LEFT JOIN（交换 FROM 和 JOIN）"""
    import sqlglot.expressions as exp

    if not isinstance(ast, exp.Select):
        return ast

    joins = ast.args.get("joins")
    if not joins:
        return ast

    new_joins = []
    for j in joins:
        side = (j.args.get("side") or "").upper()
        if side == "RIGHT":
            # 交换: FROM A RIGHT JOIN B ON cond → FROM B LEFT JOIN A ON cond
            old_from = ast.args["from"].this
            ast.set("from", exp.From(this=j.this.copy()))
            new_join = exp.Join(this=old_from.copy(), side="LEFT", on=j.args.get("on"))
            new_joins.append(new_join)
        else:
            new_joins.append(j)

    ast.set("joins", new_joins)
    return ast


def _transform_full_joins(ast: sqlglot.Expression) -> sqlglot.Expression:
    """将 FULL OUTER JOIN 转换为 (LEFT JOIN) UNION (LEFT JOIN)"""
    import sqlglot.expressions as exp

    def _has_full_join(node):
        if isinstance(node, exp.Select):
            for j in (node.args.get("joins") or []):
                if (j.args.get("side") or "").upper() == "FULL":
                    return True
        return False

    # 只处理最外层 FULL JOIN
    if not isinstance(ast, exp.Select) or not _has_full_join(ast):
        return ast

    # 找到 FULL JOIN 并拆解
    joins = list(ast.args.get("joins") or [])
    new_joins = []
    union_branches = []

    for j in joins:
        side = (j.args.get("side") or "").upper()
        if side == "FULL":
            # 保存原始 JOIN 信息
            right_table = j.this
            on_cond = j.args.get("on")

            # 左支: A LEFT JOIN B → 保持不变，改 side 为 LEFT
            left_branch_join = j.copy()
            left_branch_join.set("side", "LEFT")

            # 右支: B LEFT JOIN A → 交换左右，改 side 为 LEFT
            # 构建右支的 FROM + JOIN
            right_branch = exp.Select()
            right_branch.set("from", exp.From(this=right_table.copy()))
            right_branch.set("joins", [
                exp.Join(
                    this=ast.args["from"].this.copy(),
                    side="LEFT",
                    on=on_cond.copy() if on_cond else None,
                )
            ])
            # 复制外层 projection 到右支
            right_branch.set("expressions", [
                e.copy() for e in (ast.expressions or [])
            ])
            union_branches.append(right_branch)

            # 左支使用修改后的 JOIN
            new_joins.append(left_branch_join)
        else:
            new_joins.append(j)

    if not union_branches:
        return ast

    # 构建 UNION
    ast.set("joins", new_joins)
    branches = [ast] + union_branches

    # 构建 UNION 树
    result = branches[0]
    for b in branches[1:]:
        result = exp.Union(this=result, expression=b, distinct=True)

    return result
