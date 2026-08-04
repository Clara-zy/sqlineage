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
    _check_no_limit(ast)


def _check_no_limit(node: exp.Expression):
    if isinstance(node, exp.Limit):
        raise ValidationError(49001, "SQL 中包含 LIMIT，禁止入血缘")
    if isinstance(node, exp.Select):
        for child_node in node.walk():
            if isinstance(child_node, exp.Limit):
                raise ValidationError(49001, "SQL 中包含 LIMIT，禁止入血缘")
    ctes = node.args.get("with")
    if ctes:
        for cte in (ctes.expressions or []):
            _check_no_limit(cte.this)


def validate_join_rules(ast: exp.Expression):
    """
    校验 JOIN 规范。
    RIGHT/FULL JOIN 由 normalizer 前置转换，不应到达此处；
    连续 INNER JOIN 和 INNER+LEFT 混合均已放开。
    """
    pass


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
