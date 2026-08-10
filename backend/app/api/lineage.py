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
            "FROM build_history ORDER BY id DESC LIMIT 500"
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

                # 收集所有可达的 field hash（先收集顶层子查询）
                reachable = set()
                sq_queue: list[str] = list(sq_hashes)

                # 递归展开 UNION 的 branches
                while sq_queue:
                    sq_h = sq_queue.pop(0)
                    cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (sq_h,))
                    sq_row = cur.fetchone()
                    if not sq_row:
                        continue
                    struct = sq_row["structure"]
                    for fh in struct.get("fields", []):
                        if fh not in reachable:
                            reachable.add(fh)
                    # UNION branches + 内嵌子查询
                    for branch_h in struct.get("branches", []):
                        if branch_h not in reachable:
                            sq_queue.append(branch_h)
                    for inner_h in struct.get("inner_subqueries", []):
                        if inner_h not in reachable:
                            sq_queue.append(inner_h)

                # BFS 沿 field_references 向下追溯
                queue = list(reachable)
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


@router.get("/overview")
def overview(
    query_ids: str | None = None,
    user: dict = Depends(get_current_user),
):
    """返回纯表→表关系图谱（表级视图）。

    从选中测例的 join ON 条件中提取列对，构建表间关联边：
    - 节点：仅表节点（含列结构元数据供前端展示详情）
    - 边：表↔表关联，标签为 ON 列对，如 [t1.col1]-[t2.col1]
    - 相同表对 + 相同列对 → 合并为一条边
    """
    import json as _json

    if not query_ids:
        return {"code": 0, "data": {"nodes": [], "edges": []}, "message": "ok"}

    ids = [int(x) for x in query_ids.split(",") if x.strip().isdigit()]
    if not ids:
        return {"code": 0, "data": {"nodes": [], "edges": []}, "message": "ok"}

    with get_cursor() as cur:
        # 1. 获取根子查询 hash
        ph = ",".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT subquery_hash FROM build_history WHERE id IN ({ph})", ids
        )
        sq_hashes = [r["subquery_hash"] for r in cur.fetchall()]
        if not sq_hashes:
            return {"code": 0, "data": {"nodes": [], "edges": []}, "message": "ok"}

        # 2. BFS 收集所有子查询
        all_sq: set[str] = set()
        sq_queue = list(sq_hashes)
        while sq_queue:
            sq_h = sq_queue.pop(0)
            if sq_h in all_sq:
                continue
            all_sq.add(sq_h)
            cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (sq_h,))
            row = cur.fetchone()
            if not row:
                continue
            struct = row["structure"]
            for inner_h in struct.get("inner_subqueries", []):
                if inner_h not in all_sq:
                    sq_queue.append(inner_h)
            for branch_h in struct.get("branches", []):
                if branch_h not in all_sq:
                    sq_queue.append(branch_h)

        # 3. 每个子查询 BFS 字段 DAG，从 join 节点提取列对
        raw_table_names: set[str] = set()
        edge_candidates: list[tuple[str, str, frozenset]] = []

        for sq_h in all_sq:
            cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (sq_h,))
            row = cur.fetchone()
            if not row:
                continue
            struct = row["structure"]
            root_fields = struct.get("fields", [])

            field_queue = list(root_fields)
            visited_fields = set(field_queue)
            local_exprs: dict[str, dict] = {}

            while field_queue:
                fh = field_queue.pop(0)
                cur.execute("SELECT expression FROM fields WHERE hash = %s", (fh,))
                frow = cur.fetchone()
                if not frow:
                    continue
                expr = frow["expression"]
                if isinstance(expr, str):
                    expr = _json.loads(expr)
                local_exprs[fh] = expr

                if expr.get("op") == "column":
                    table = expr.get("table", "")
                    if table:
                        raw_table_names.add(table)

                cur.execute(
                    "SELECT referenced_hash FROM field_references "
                    "WHERE referrer_hash = %s",
                    (fh,),
                )
                for ref_row in cur.fetchall():
                    ref = ref_row["referenced_hash"]
                    if ref not in visited_fields:
                        visited_fields.add(ref)
                        field_queue.append(ref)

            # 从 join 节点提取列对
            for fh, expr in local_exprs.items():
                if expr.get("op") != "join":
                    continue
                on_hash = expr.get("on")
                if not on_hash:
                    continue
                pairs = _extract_on_column_pairs(on_hash, local_exprs, cur)
                if not pairs:
                    continue

                tables_in_on: set[str] = set()
                for ca, cb in pairs:
                    tables_in_on.add(ca[0])
                    tables_in_on.add(cb[0])

                if len(tables_in_on) >= 2:
                    tl = sorted(tables_in_on)
                    for i in range(len(tl)):
                        for j in range(i + 1, len(tl)):
                            edge_candidates.append(
                                (tl[i], tl[j], frozenset(pairs))
                            )

        if not raw_table_names:
            return {"code": 0, "data": {"nodes": [], "edges": []}, "message": "ok"}

        # 4. 表名归一化 + 合并边
        name_to_full: dict[str, str] = {}
        tn_list = list(raw_table_names)
        tn_ph = ",".join(["%s"] * len(tn_list))
        cur.execute(
            f"SELECT d.name AS db_name, t.table_name FROM tables t "
            f"JOIN databases d ON d.id = t.database_id "
            f"WHERE (d.name || '.' || t.table_name) IN ({tn_ph}) "
            f"   OR t.table_name IN ({tn_ph})",
            tn_list + tn_list,
        )
        for r in cur.fetchall():
            db = r["db_name"]
            tn = r["table_name"]
            full = f"{db}.{tn}"
            name_to_full[tn] = full
            name_to_full[full] = full

        def _resolve(tn: str) -> str:
            return name_to_full.get(tn, tn)

        normalized_edges: dict[tuple[str, str, frozenset], int] = {}
        for ta, tb, pairs in edge_candidates:
            ta_n = _resolve(ta)
            tb_n = _resolve(tb)
            if ta_n == tb_n:
                continue
            key = (min(ta_n, tb_n), max(ta_n, tb_n), pairs)
            normalized_edges[key] = normalized_edges.get(key, 0) + 1

        all_tables: set[str] = set()
        for ta, tb, _ in normalized_edges:
            all_tables.add(ta)
            all_tables.add(tb)

        # 5. 构建表节点（含列结构）
        nodes = []
        for tn in sorted(all_tables):
            parts = tn.rsplit(".", 1)
            db_name = parts[0] if len(parts) == 2 else ""
            short_name = parts[-1]

            columns = []
            cur.execute(
                "SELECT t.id FROM tables t JOIN databases d ON d.id = t.database_id "
                "WHERE d.name = %s AND t.table_name = %s",
                (db_name, short_name),
            )
            trow = cur.fetchone()
            table_id = trow["id"] if trow else None

            if table_id:
                cur.execute(
                    "SELECT column_name, column_type FROM table_columns "
                    "WHERE table_id = %s ORDER BY ordinal_position",
                    (table_id,),
                )
                columns = [
                    {"name": r["column_name"], "type": r["column_type"]}
                    for r in cur.fetchall()
                ]

            nodes.append({
                "id": f"table:{tn}",
                "type": "tableNode",
                "data": {
                    "db_name": db_name,
                    "table_name": short_name,
                    "full_name": tn,
                    "table_id": table_id,
                    "columns": columns,
                },
            })

        # 6. 构建边：列对 → 标签 [col1]-[col2]
        edges = []
        for (ta, tb, pairs), _ in sorted(normalized_edges.items()):
            pair_labels = []
            for ca, cb in sorted(pairs):
                pair_labels.append(f"[{ca[1]}]-[{cb[1]}]")
            edges.append({
                "id": f"{ta}=={tb}",
                "source": f"table:{ta}",
                "target": f"table:{tb}",
                "label": "  ".join(pair_labels),
            })

    return {"code": 0, "data": {"nodes": nodes, "edges": edges}, "message": "ok"}


def _extract_on_column_pairs(
    on_hash: str,
    exprs: dict[str, dict],
    cur,
) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """从 ON 条件中提取列对 [(('t1','c1'), ('t2','c1')), ...]"""
    import json as _j
    pairs: list[tuple[tuple[str, str], tuple[str, str]]] = []

    def _resolve_col(fh: str) -> tuple[str, str] | None:
        seen: set[str] = set()
        queue = [fh]
        while queue:
            h = queue.pop(0)
            if h in seen:
                continue
            seen.add(h)
            e = exprs.get(h)
            if not e:
                cur.execute(
                    "SELECT expression FROM fields WHERE hash = %s", (h,)
                )
                erow = cur.fetchone()
                if erow:
                    e = erow["expression"]
                    if isinstance(e, str):
                        e = _j.loads(e)
                    exprs[h] = e
            if not e:
                continue
            if e.get("op") == "column":
                return (e.get("table", ""), e.get("column", ""))
            for arg in e.get("args", []):
                child = arg.get("hash", "") if isinstance(arg, dict) else ""
                if child:
                    queue.append(child)
        return None

    seen: set[str] = set()
    queue = [on_hash]
    while queue:
        h = queue.pop(0)
        if h in seen:
            continue
        seen.add(h)
        e = exprs.get(h)
        if not e:
            cur.execute("SELECT expression FROM fields WHERE hash = %s", (h,))
            erow = cur.fetchone()
            if erow:
                e = erow["expression"]
                if isinstance(e, str):
                    e = _j.loads(e)
                exprs[h] = e
        if not e:
            continue
        op = e.get("op", "")
        if op in ("eq", "neq", "gt", "lt", "gte", "lte"):
            args = e.get("args", [])
            if len(args) >= 2:
                c1 = _resolve_col(args[0].get("hash", ""))
                c2 = _resolve_col(args[1].get("hash", ""))
                if c1 and c2 and c1[0] != c2[0]:
                    pairs.append((c1, c2))
        for arg in e.get("args", []):
            child = arg.get("hash", "") if isinstance(arg, dict) else ""
            if child and child not in seen:
                queue.append(child)

    return pairs


@router.get("/field-sql/{hash}")
def field_sql(
    hash: str,
    query_id: int | None = None,
    user: dict = Depends(get_current_user),
):
    """将单个字段节点还原为最小完整 SQL。

    query_id 可选：限制仅在该测例的子查询范围内追溯结构包裹。
    """
    from app.sql_reconstructor import field_to_sql_full
    try:
        sq_hash = None
        if query_id:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT subquery_hash FROM build_history WHERE id = %s",
                    (query_id,),
                )
                row = cur.fetchone()
                if row:
                    sq_hash = row["subquery_hash"]
        sql = field_to_sql_full(hash, sq_hash)
        return {"code": 0, "data": {"hash": hash, "sql": sql}, "message": "ok"}
    except Exception as e:
        return {"code": 500, "data": None, "message": str(e)}


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
    elif op == "join":
        return "JOIN(...)"
    elif op == "where":
        return "WHERE(...)"
    elif op == "having":
        return "HAVING(...)"
    elif op == "limt":
        return "LIMIT(...)"
    elif op == "ordr":
        return "ORDER BY"
    elif op == "current_timestamp":
        return "NOW()"
    elif op in ("and", "or"):
        return op.upper()
    elif op in ("eq", "neq", "gt", "lt", "gte", "lte", "in", "notin"):
        return op
    else:
        return op
