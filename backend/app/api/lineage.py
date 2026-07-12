"""血缘 API — build / trace / graph"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.lineage_engine.validators import ValidationError

router = APIRouter(prefix="/api/v1/lineage", tags=["lineage"])


class BuildRequest(BaseModel):
    sql: str
    sink_table: str | None = None
    comment: str | None = None


@router.post("/build")
def build(req: BuildRequest, user: dict = Depends(get_current_user)):
    try:
        result = build_lineage(req.sql, req.sink_table, req.comment)
        return {"code": 0, "data": result, "message": "ok"}
    except ValidationError as e:
        return {"code": e.code, "data": None, "message": e.message}
    except Exception as e:
        return {"code": 500, "data": None, "message": str(e)}


@router.get("/history")
def history(user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, sql_text, comment, subquery_hash, field_count, sq_count, created_at "
            "FROM build_history ORDER BY id DESC LIMIT 20"
        )
        items = [dict(r) for r in cur.fetchall()]
    return {"code": 0, "data": {"items": items}, "message": "ok"}


@router.get("/graph")
def graph(
    query_ids: str | None = None,
    user: dict = Depends(get_current_user),
):
    """返回字段节点 + 引用边。query_ids 可选过滤（逗号分隔的 build_history id）。"""
    with get_cursor() as cur:
        if query_ids:
            ids = [int(x) for x in query_ids.split(",") if x.strip().isdigit()]
            if ids:
                # 找到这些查询的顶层子查询 hash
                placeholders = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"SELECT subquery_hash FROM build_history WHERE id IN ({placeholders})",
                    ids,
                )
                sq_hashes = [r["subquery_hash"] for r in cur.fetchall()]

                if not sq_hashes:
                    return {"code": 0, "data": {"nodes": [], "edges": []}, "message": "ok"}

                # 从子查询的 structure.fields 出发，BFS 收集所有可达字段
                sq_placeholders = ",".join(["%s"] * len(sq_hashes))
                cur.execute(
                    f"SELECT structure FROM subqueries WHERE hash IN ({sq_placeholders})",
                    sq_hashes,
                )
                sq_structures = [r["structure"] for r in cur.fetchall()]

                # 收集所有可达的 field hash
                reachable = set()
                queue = []
                for struct in sq_structures:
                    for fh in struct.get("fields", []):
                        reachable.add(fh)
                        queue.append(fh)
                    wh = struct.get("where")
                    if wh:
                        reachable.add(wh)
                        queue.append(wh)

                # BFS
                while queue:
                    fh = queue.pop(0)
                    cur.execute(
                        "SELECT referenced_hash FROM field_references WHERE referrer_hash = %s",
                        (fh,),
                    )
                    for row in cur.fetchall():
                        ref = row["referenced_hash"]
                        if ref not in reachable:
                            reachable.add(ref)
                            queue.append(ref)

                # 过滤节点和边
                if reachable:
                    ph = ",".join(["%s"] * len(reachable))
                    cur.execute(
                        f"SELECT hash, field_type, expression FROM fields WHERE hash IN ({ph})",
                        list(reachable),
                    )
                    field_rows = [dict(r) for r in cur.fetchall()]

                    cur.execute(
                        f"SELECT referenced_hash AS source, referrer_hash AS target "
                        f"FROM field_references "
                        f"WHERE referenced_hash IN ({ph}) AND referrer_hash IN ({ph})",
                        list(reachable) * 2,
                    )
                    edge_rows = [dict(r) for r in cur.fetchall()]
                else:
                    field_rows = []
                    edge_rows = []
            else:
                field_rows = []
                edge_rows = []
        else:
            cur.execute(
                "SELECT hash, field_type, expression FROM fields ORDER BY created_at"
            )
            field_rows = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT referenced_hash AS source, referrer_hash AS target "
                "FROM field_references"
            )
            edge_rows = [dict(r) for r in cur.fetchall()]

    # 序列化 nodes / edges
    nodes = []
    for i, f in enumerate(field_rows):
        expr = f["expression"]
        if isinstance(expr, str):
            import json
            expr = json.loads(expr)
        op = expr.get("op", "?")
        label = _node_label(f["field_type"], expr)
        nodes.append({
            "id": f["hash"],
            "type": "fieldNode",
            "position": {"x": (i % 8) * 200, "y": (i // 8) * 120},
            "data": {
                "hash": f["hash"][:8],
                "field_type": f["field_type"],
                "label": label,
                "op": op,
            },
        })

    edges = [
        {
            "id": f"{e['source']}-{e['target']}",
            "source": e["source"],
            "target": e["target"],
        }
        for e in edge_rows
    ]

    return {"code": 0, "data": {"nodes": nodes, "edges": edges}, "message": "ok"}


@router.get("/node/{hash}")
def node_detail(hash: str, user: dict = Depends(get_current_user)):
    """获取单个节点详情（含上下游）"""
    import json as _json

    with get_cursor() as cur:
        cur.execute(
            "SELECT hash, field_type, expression FROM fields WHERE hash = %s", (hash,)
        )
        row = cur.fetchone()
        if not row:
            return {"code": 404, "data": None, "message": "节点不存在"}

        expr = row["expression"]
        if isinstance(expr, str):
            expr = _json.loads(expr)

        # 上游（被引用者）
        cur.execute(
            "SELECT f.hash, f.field_type, f.expression FROM field_references fr "
            "JOIN fields f ON f.hash = fr.referenced_hash "
            "WHERE fr.referrer_hash = %s",
            (hash,),
        )
        upstream = [
            {
                "hash": r["hash"],
                "field_type": r["field_type"],
                "label": _node_label(r["field_type"],
                    _json.loads(r["expression"]) if isinstance(r["expression"], str) else r["expression"]),
            }
            for r in cur.fetchall()
        ]

        # 下游（引用者）
        cur.execute(
            "SELECT f.hash, f.field_type, f.expression FROM field_references fr "
            "JOIN fields f ON f.hash = fr.referrer_hash "
            "WHERE fr.referenced_hash = %s",
            (hash,),
        )
        downstream = [
            {
                "hash": r["hash"],
                "field_type": r["field_type"],
                "label": _node_label(r["field_type"],
                    _json.loads(r["expression"]) if isinstance(r["expression"], str) else r["expression"]),
            }
            for r in cur.fetchall()
        ]

    return {
        "code": 0,
        "data": {
            "hash": row["hash"],
            "field_type": row["field_type"],
            "expression": expr,
            "upstream": upstream,
            "downstream": downstream,
        },
        "message": "ok",
    }


@router.get("/upgrade/preview/{table_name}")
def upgrade_preview(
    table_name: str,
    user: dict = Depends(get_current_user),
):
    """预览升级影响"""
    if user["role"] != "admin":
        return {"code": 403, "data": None, "message": "仅管理员可操作"}

    from app.upgrade_engine.engine import preview_upgrade
    result = preview_upgrade(table_name)
    if "error" in result:
        return {"code": 500, "data": None, "message": result["error"]}
    return {"code": 0, "data": result, "message": "ok"}


@router.post("/upgrade/execute")
def upgrade_execute(
    req: dict,
    user: dict = Depends(get_current_user),
):
    """执行升级"""
    if user["role"] != "admin":
        return {"code": 403, "data": None, "message": "仅管理员可操作"}

    table_name = req.get("table_name")
    extended_sql = req.get("extended_sql")
    if not table_name or not extended_sql:
        return {"code": 400, "data": None, "message": "缺少参数"}

    from app.upgrade_engine.engine import execute_upgrade
    try:
        result = execute_upgrade(table_name, extended_sql)
        if "error" in result:
            return {"code": 500, "data": None, "message": result["error"]}
        return {"code": 0, "data": result, "message": "ok"}
    except Exception as e:
        return {"code": 500, "data": None, "message": str(e)}


@router.post("/clear")
def clear(user: dict = Depends(get_current_user)):
    """清空所有血缘数据（保留表结构）"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM field_references")
        cur.execute("DELETE FROM table_column_mappings")
        cur.execute("DELETE FROM table_input_subqueries")
        cur.execute("DELETE FROM natural_semantics")
        cur.execute("DELETE FROM subqueries")
        cur.execute("DELETE FROM fields")
        cur.execute("UPDATE table_columns SET field_hash = NULL, updated_at = now()")
        cur.execute("UPDATE tables SET input_subquery_hash = NULL, updated_at = now()")
    return {"code": 0, "data": None, "message": "ok"}


def _node_label(field_type: str, expr: dict) -> str:
    op = expr.get("op", "?")
    if op == "column":
        return f"{expr.get('table', '')}.{expr.get('column', '')}"
    elif op == "alias":
        return f"AS {expr.get('alias_name', '?')}"
    elif op == "literal":
        v = str(expr.get("value", ""))
        return f"'{v[:12]}'"
    elif op == "star":
        return "*"
    elif op == "casewhen":
        return "CASE WHEN"
    elif op in ("JOIN",):
        return "JOIN(...)"
    elif op in ("and", "or"):
        return op.upper()
    elif op in ("eq", "neq", "gt", "lt", "gte", "lte", "in", "notin"):
        return op
    else:
        return op
