"""交互式血缘探索 — 构建 SQL 并查看数据库结果

用法:
    cd backend && PYTHONPATH=. ../.venv/bin/python app/explore.py "<SQL>"

示例:
    env PYTHONPATH=backend backend/.venv/bin/python backend/app/explore.py "SELECT trans_id, mchnt_cd FROM trans_flow"

输出:
    - 新建 / 复用字段数
    - 字段列表 (hash / 类型 / 表达式摘要)
    - 子查询列表
    - 引用关系
"""

import sys
from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.seed import seed


def _summarize(expr: dict) -> str:
    """将 expression JSON 转为可读摘要"""
    op = expr.get("op", "?")
    if op == "column":
        return f"{expr.get('table', '')}.{expr.get('column', '')}"
    elif op == "alias":
        return f"alias({expr.get('alias_name', '?')})"
    elif op == "literal":
        return f"'{expr.get('value', '')}'"
    elif op == "JOIN":
        on_h = expr.get("on") or ""
        lateral = ", lateral" if expr.get("lateral") else ""
        return f"JOIN(on={str(on_h)[:8]}...{lateral})"
    elif op == "window":
        return f"window({expr.get('frame', '')})"
    elif op in ("nvl", "round", "concat", "substring", "cast", "sum", "count", "avg", "min", "max"):
        return f"{op}(...)"
    elif op in ("and", "or"):
        return f"{op}(...)"
    elif op in ("eq", "neq", "gt", "lt", "gte", "lte", "in", "notin"):
        return f"{op}(...)"
    elif op == "casewhen":
        return "CASE WHEN ... END"
    elif op == "WHERE":
        return "WHERE(...)"
    elif op == "current_timestamp":
        return "CURRENT_TIMESTAMP"
    elif op == "star":
        return "*"
    else:
        args_n = len(expr.get("args", []))
        return f"{op}({args_n} args)"


def main():
    if len(sys.argv) < 2:
        print("用法: python explore.py '<SQL>'")
        print("示例: python explore.py 'SELECT trans_id, nvl(mchnt_cd, \"UNKNOWN\") FROM trans_flow'")
        sys.exit(1)

    sql = sys.argv[1]

    # 确保 seed 数据存在
    seed()

    # 构建血缘
    print(f"\n{'='*60}")
    print(f"SQL: {sql[:100]}{'...' if len(sql) > 100 else ''}")
    print(f"{'='*60}")

    try:
        result = build_lineage(sql)
    except Exception as e:
        print(f"\n❌ 构建失败: {e}")
        sys.exit(1)

    print(f"\n📊 结果:")
    print(f"   新建字段: {result['new_fields']}")
    print(f"   新建子查询: {result['new_subqueries']}")
    print(f"   复用节点: {result['reused_nodes']}")

    # 查看新增的字段
    with get_cursor() as cur:
        cur.execute(
            "SELECT hash, field_type, expression "
            "FROM fields ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
        if rows:
            print(f"\n📋 所有字段 (最近创建在前):")
            print(f"   {'hash':<18} {'type':<20} {'summary'}")
            print(f"   {'-'*18} {'-'*20} {'-'*30}")
            for r in rows[:20]:
                expr = r["expression"]
                if isinstance(expr, str):
                    import json
                    expr = json.loads(expr)
                summary = _summarize(expr)
                print(f"   {r['hash']:<18} {r['field_type']:<20} {summary}")
            if len(rows) > 20:
                print(f"   ... 还有 {len(rows) - 20} 条")

        # 查看引用关系
        cur.execute("SELECT count(*) FROM field_references")
        ref_count = cur.fetchone()["count"]
        print(f"\n🔗 field_references: {ref_count} 条边")

        # 查看子查询
        cur.execute(
            "SELECT hash, subquery_type, "
            "jsonb_array_length(structure -> 'fields') AS field_count "
            "FROM subqueries ORDER BY created_at DESC"
        )
        sq_rows = cur.fetchall()
        if sq_rows:
            print(f"\n📦 子查询:")
            for r in sq_rows[:5]:
                print(f"   {r['hash']:<18} {r['subquery_type']:<8} {r['field_count']} 个投影字段")

    print(f"\n✅ 数据已持久化到 sqlineage 数据库")


if __name__ == "__main__":
    main()
