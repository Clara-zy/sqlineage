"""表结构 API — 从 DDL 构建 / CRUD"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.database import get_cursor

router = APIRouter(prefix="/api/v1/tables", tags=["tables"])


class CreateTableRequest(BaseModel):
    ddl: str
    source: str = "root"  # root | middle


@router.get("")
def list_tables(
    source: str | None = Query(None),
    keyword: str | None = Query(None),
    page: int = 1,
    page_size: int = 20,
    user: dict = Depends(get_current_user),
):
    page_size = min(max(page_size, 1), 100)
    with get_cursor() as cur:
        conditions = []
        params = []
        if source:
            conditions.append("t.source = %s")
            params.append(source)
        if keyword:
            conditions.append(
                "(t.table_name ILIKE %s OR d.name ILIKE %s OR t.logical_name ILIKE %s)"
            )
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        where = " AND ".join(conditions) if conditions else "TRUE"
        cur.execute(
            f"SELECT count(*) FROM tables t JOIN databases d ON d.id = t.database_id WHERE {where}",
            params,
        )
        total = cur.fetchone()["count"]

        cur.execute(
            f"SELECT t.id, d.name AS db_name, t.table_name, t.table_type, t.source, t.logical_name, "
            f"(SELECT count(*) FROM table_columns tc WHERE tc.table_id = t.id) AS col_count, "
            f"t.created_at, t.updated_at, "
            f"cu.username AS created_by_name, uu.username AS updated_by_name "
            f"FROM tables t "
            f"JOIN databases d ON d.id = t.database_id "
            f"LEFT JOIN users cu ON cu.id = t.created_by "
            f"LEFT JOIN users uu ON uu.id = t.updated_by "
            f"WHERE {where} ORDER BY t.id LIMIT %s OFFSET %s",
            params + [page_size, (page - 1) * page_size],
        )
        items = [dict(r) for r in cur.fetchall()]

    return {
        "code": 0,
        "data": {"items": items, "total": total, "page": page, "page_size": page_size},
        "message": "ok",
    }


@router.post("")
def create_table(
    req: CreateTableRequest,
    user: dict = Depends(get_current_user),
):
    if user["role"] not in ("admin", "user"):
        raise HTTPException(status_code=403, detail="权限不足")

    # 解析 DDL
    parsed = _parse_ddl(req.ddl)
    if not parsed:
        return {"code": 41002, "data": None, "message": "DDL 解析失败"}

    table_name = parsed["table_name"]
    columns = parsed["columns"]

    with get_cursor() as cur:
        # 检查重复
        cur.execute(
            "SELECT id FROM tables WHERE table_name = %s",
            (table_name,),
        )
        if cur.fetchone():
            return {
                "code": 41001,
                "data": None,
                "message": f"表 {table_name} 已存在",
            }

        # 插入表
        cur.execute(
            "INSERT INTO tables (schema_name, table_name, table_type, source) "
            "VALUES ('', %s, '事实表', %s) RETURNING id",
            (table_name, req.source),
        )
        table_id = cur.fetchone()["id"]

        # 插入列
        col_list = []
        for i, (col_name, col_type, col_comment) in enumerate(columns, 1):
            cur.execute(
                "INSERT INTO table_columns "
                "(table_id, column_name, column_type, column_comment, ordinal_position) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (table_id, col_name, col_type, col_comment, i),
            )
            col_list.append(
                {
                    "column_name": col_name,
                    "column_type": col_type,
                    "ordinal_position": i,
                }
            )

        # root 表自动构建 atomic 字段
        from app.lineage_engine.engine import build_lineage
        from app.lineage_engine.context import BuildContext
        from app.lineage_engine.field_builder import build_column, _maybe_parse_enum
        from app.lineage_engine.context import compute_hash
        import sqlglot.expressions as exp
        import json

        if req.source == "root":
            ctx = BuildContext()
            for i, (col_name, col_type, col_comment) in enumerate(columns, 1):
                expression = {"op": "column", "table": table_name, "column": col_name}
                h = compute_hash(expression)
                ctx.upsert_field(h, "atomic", expression)
                # 枚举值
                _maybe_parse_enum(table_id, col_name, h)
                # 回填 field_hash
                cur.execute(
                    "UPDATE table_columns SET field_hash = %s "
                    "WHERE table_id = %s AND column_name = %s",
                    (h, table_id, col_name),
                )

    return {
        "code": 0,
        "data": {
            "table": {
                "id": table_id,
                "table_name": table_name,
                "source": req.source,
            },
            "columns": col_list,
        },
        "message": "ok",
    }


@router.get("/{table_id}")
def get_table(table_id: int, user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute(
            "SELECT t.id, d.name AS db_name, t.table_name, t.table_type, t.source, "
            "t.logical_name, t.input_subquery_hash, t.created_at "
            "FROM tables t JOIN databases d ON d.id = t.database_id WHERE t.id = %s",
            (table_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"code": 404, "data": None, "message": "表不存在"}

        table = dict(row)
        cur.execute(
            "SELECT id, column_name, column_type, column_comment, "
            "ordinal_position, field_hash "
            "FROM table_columns WHERE table_id = %s ORDER BY ordinal_position",
            (table_id,),
        )
        table["columns"] = [dict(r) for r in cur.fetchall()]

    return {"code": 0, "data": table, "message": "ok"}


@router.get("/{table_id}/input-history")
def input_history(table_id: int, user: dict = Depends(get_current_user)):
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, subquery_hash, status, deprecated_at, deprecated_reason, created_at "
            "FROM table_input_subqueries WHERE table_id = %s ORDER BY created_at DESC",
            (table_id,),
        )
        items = [dict(r) for r in cur.fetchall()]
    return {"code": 0, "data": {"items": items}, "message": "ok"}


@router.get("/{table_id}/preview-delete")
def preview_delete_table(table_id: int, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    with get_cursor() as cur:
        cur.execute(
            "SELECT t.table_name, d.name AS db_name, "
            "(SELECT count(*) FROM fields f JOIN table_columns tc ON tc.field_hash = f.hash WHERE tc.table_id = t.id) AS field_count "
            "FROM tables t JOIN databases d ON d.id = t.database_id WHERE t.id = %s",
            (table_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404)
        return {"code": 0, "data": dict(row), "message": "ok"}


@router.delete("/{table_id}")
def delete_table(table_id: int, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    with get_cursor() as cur:
        cur.execute("DELETE FROM table_columns WHERE table_id = %s", (table_id,))
        cur.execute("DELETE FROM table_input_subqueries WHERE table_id = %s", (table_id,))
        cur.execute("DELETE FROM table_column_mappings WHERE table_column_id IN (SELECT id FROM table_columns WHERE table_id = %s)", (table_id,))
        cur.execute("DELETE FROM tables WHERE id = %s", (table_id,))
    return {"code": 0, "data": None, "message": "ok"}


@router.post("/{table_id}/downgrade")
def downgrade_to_root(table_id: int, user: dict = Depends(get_current_user)):
    """sink → root 降级"""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    with get_cursor() as cur:
        cur.execute("UPDATE tables SET source = 'root', input_subquery_hash = NULL WHERE id = %s AND source = 'sink'", (table_id,))
    return {"code": 0, "data": None, "message": "ok"}


@router.get("/{table_id}/columns/{col_id}/mapping-history")
def mapping_history(
    table_id: int, col_id: int, user: dict = Depends(get_current_user)
):
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, field_hash, status, deprecated_at, deprecated_reason, created_at "
            "FROM table_column_mappings WHERE table_column_id = %s ORDER BY created_at DESC",
            (col_id,),
        )
        items = [dict(r) for r in cur.fetchall()]
    return {"code": 0, "data": {"items": items}, "message": "ok"}


def _parse_ddl(ddl: str) -> dict | None:
    """简易 DDL 解析 — 提取表名和列定义"""
    import re

    # CREATE TABLE [schema.]table_name (...)
    m = re.search(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\.(\w+)\s*\((.+)\)",
        ddl, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        # 兼容旧格式（无库名）
        m = re.search(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.+)\)",
            ddl, re.IGNORECASE | re.DOTALL,
        )
        if m:
            db_name = "mydb"
            table_name = m.group(1)
            body = m.group(2)
        else:
            return None
    else:
        db_name = m.group(1)
        table_name = m.group(2)
        body = m.group(3)
    if not m and not table_name:
        return None

    columns = []
    # 简易列解析: col_name col_type [COMMENT '...']
    col_pattern = re.compile(
        r"(\w+)\s+(\w+(?:\([^)]*\))?)\s*(?:COMMENT\s+'([^']*)')?",
        re.IGNORECASE,
    )
    for cm in col_pattern.finditer(body):
        col_name = cm.group(1)
        col_type = cm.group(2).upper()
        col_comment = cm.group(3) or ""
        columns.append((col_name, col_type, col_comment))

    if not columns:
        return None

    return {"table_name": table_name, "columns": columns}
