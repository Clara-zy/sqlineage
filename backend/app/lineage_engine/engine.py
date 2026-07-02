"""血缘构建引擎 — Phase 协调器"""

import sqlglot
import sqlglot.expressions as exp

from app.database import get_cursor
from app.lineage_engine.context import BuildContext
from app.lineage_engine.validators import (
    ValidationError,
    validate_no_limit,
    validate_join_rules,
    validate_tables_exist,
)
from app.lineage_engine.normalizer import build_schema_dict, normalize
from app.lineage_engine.field_builder import build_field, build_alias_map
from app.lineage_engine.subquery_builder import build_subquery_select


def build_lineage(sql: str, sink_table: str | None = None) -> dict:
    """完整血缘构建流程"""
    # Phase 0: 入参校验
    validate_no_limit(sql)
    ast = sqlglot.parse_one(sql)

    ctx = BuildContext()
    validate_join_rules(ast)
    validate_tables_exist(ast, ctx)

    # Phase 2: 标准化
    schema = build_schema_dict()
    normalized_ast = normalize(sql, schema)

    # 统计构建前的节点数
    with get_cursor() as cur:
        cur.execute("SELECT count(*) FROM fields")
        fields_before = cur.fetchone()["count"]
        cur.execute("SELECT count(*) FROM subqueries")
        sq_before = cur.fetchone()["count"]

    # Phase 3 + 4: 构建字段 + 子查询
    _build_ctes(normalized_ast, ctx)

    if isinstance(normalized_ast, exp.Select):
        select_ast = normalized_ast
        build_alias_map(select_ast, ctx)
        sq_hash, _ = build_subquery_select(select_ast, ctx)
    elif isinstance(normalized_ast, exp.Union):
        from app.lineage_engine.subquery_builder import build_subquery_union
        sq_hash = build_subquery_union(normalized_ast, ctx)
    else:
        for node in normalized_ast.walk():
            if isinstance(node, exp.Select):
                select_ast = node
                break
        build_alias_map(select_ast, ctx)
        sq_hash, _ = build_subquery_select(select_ast, ctx)

    # 统计结果
    with get_cursor() as cur:
        cur.execute("SELECT count(*) FROM fields")
        fields_after = cur.fetchone()["count"]
        cur.execute("SELECT count(*) FROM subqueries")
        sq_after = cur.fetchone()["count"]

    # Phase 5: 绑定 sink 表
    sink_result = None
    if sink_table:
        sink_result = _bind_sink_table(sink_table, select_ast, sq_hash, ctx)

    return {
        "new_fields": fields_after - fields_before,
        "new_subqueries": sq_after - sq_before,
        "reused_nodes": fields_before + sq_before,
        "sink_binding": sink_result,
    }


def _build_ctes(ast, ctx):
    """构建 CTE (WITH) 子查询"""
    ctes = ast.args.get("with")
    if not ctes:
        return
    for cte in (ctes.expressions or []):
        alias = cte.alias
        if hasattr(alias, "name"):
            alias = alias.name
        inner = cte.this
        if alias and isinstance(inner, exp.Select):
            sq_hash, alias_to_hash = build_subquery_select(inner, ctx)
            ctx.alias_map[alias] = f"__subquery__{sq_hash}"
            ctx.subquery_map[alias] = {"sq_hash": sq_hash, "ast": inner, "alias_to_hash": alias_to_hash}


def _bind_sink_table(sink_ref: str, select_ast, sq_hash: str, ctx: BuildContext) -> dict:
    """Phase 5: 绑定输出表"""
    parts = sink_ref.split(".", 1)
    schema_name = parts[0] if len(parts) == 2 else ""
    table_name = parts[-1]

    table = ctx.get_table(schema_name, table_name)
    if not table:
        raise ValidationError(49003, f"sink 表 {sink_ref} 不存在")

    tc_list = ctx.get_table_columns(table["id"])
    projection_count = len(select_ast.expressions)
    if projection_count != len(tc_list):
        raise ValidationError(
            49006,
            f"子查询投影列数({projection_count})与表结构列数({len(tc_list)})不一致",
        )

    mappings = []
    ctx.deprecate_active_input_subquery(table["id"], "新血缘构建")
    ctx.insert_input_subquery(table["id"], sq_hash)
    ctx.update_table_input_subquery(table["id"], sq_hash)

    for i, expr in enumerate(select_ast.expressions):
        tc = tc_list[i]
        field_h = build_field(expr, ctx)
        ctx.deprecate_active_mappings(tc["id"], "新血缘构建")
        ctx.insert_column_mapping(tc["id"], field_h)
        ctx.update_table_column_field_hash(tc["id"], field_h)
        mappings.append({
            "ordinal_position": tc["ordinal_position"],
            "column_name": tc["column_name"],
            "field_hash": field_h,
        })

    return {"input_subquery": sq_hash, "column_mappings": mappings}
