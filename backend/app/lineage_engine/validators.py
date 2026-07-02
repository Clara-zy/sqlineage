"""Phase 0 入参校验"""

import sqlglot
import sqlglot.expressions as exp


class ValidationError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message


# ── 校验函数 ──

def validate_no_limit(sql: str):
    """扫描 SQL 中是否包含 LIMIT 关键字"""
    try:
        ast = sqlglot.parse_one(sql)
    except Exception:
        raise ValidationError(49001, "SQL 语法错误，无法解析")
    # 处理 WITH 包裹 — 找到最内层 Select
    _check_no_limit(ast)


def _check_no_limit(node: exp.Expression):
    if isinstance(node, exp.Limit):
        raise ValidationError(49001, "SQL 中包含 LIMIT，禁止入血缘")
    if isinstance(node, exp.Select):
        for child_node in node.walk():
            if isinstance(child_node, exp.Limit):
                raise ValidationError(49001, "SQL 中包含 LIMIT，禁止入血缘")
    # 对 CTE 递归
    ctes = node.args.get("with")
    if ctes:
        for cte in (ctes.expressions or []):
            _check_no_limit(cte.this)


def validate_join_rules(ast: exp.Expression):
    """
    校验 JOIN 规范：
    - 3+ 表连续 join 只允许全部 LEFT JOIN
    - inner/right/full join 不能出现在连续 join 串中
    """
    # 递归检查每个 SELECT 的 join 链
    _check_joins(ast)


def _check_joins(node: exp.Expression):
    """递归检查 JOIN 链"""
    if isinstance(node, exp.Select):
        joins = node.args.get("joins") or []
        if len(joins) >= 2:  # 3+ 表的连续 join
            # 检查所有 join 是否都是 LEFT
            for j in joins:
                side = (j.args.get("side") or "").upper()
                kind = (j.args.get("kind") or "").upper()
                # LEFT JOIN: side="LEFT", kind="JOIN"
                # INNER JOIN: side=None, kind="INNER"
                if (side and side != "LEFT") or (kind and kind not in ("JOIN", "")):
                    detected = side or kind
                    raise ValidationError(
                        49002,
                        f"多表连续 JOIN 只允许 LEFT JOIN，检测到 {detected} JOIN",
                    )

    # 递归进入子查询
    for child in node.walk():
        if isinstance(child, exp.Select) and child is not node:
            _check_joins(child)


def validate_tables_exist(ast: exp.Expression, ctx) -> list[dict]:
    """
    提取 AST 中所有实体表引用，校验表结构已构建。
    跳过 CTE 引用和子查询别名。
    返回: list[dict] — {schema, table, source}
    """
    tables = []

    # 提前收集 CTE 名称
    cte_names = set()
    ctes = ast.args.get("with")
    if ctes:
        for cte in (ctes.expressions or []):
            if cte.alias:
                alias = cte.alias
                if hasattr(alias, "name"):
                    alias = alias.name
                cte_names.add(alias)

    for node in ast.walk():
        if isinstance(node, exp.Table):
            name = node.name
            if not name:
                continue
            db = node.args.get("db") or ""
            schema_name = db if isinstance(db, str) else (db.name if db else "")
            table_name = name if isinstance(name, str) else name.name

            if not table_name:
                continue

            # 跳过 CTE 引用
            if table_name in cte_names:
                continue

            t = ctx.get_table(schema_name or "", table_name)
            if t is None:
                raise ValidationError(
                    49003, f"引用的表 {schema_name}.{table_name} 未构建表结构"
                )
            tables.append(t)

    return tables
