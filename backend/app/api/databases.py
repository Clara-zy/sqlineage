"""数据库管理 API"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.database import get_cursor

router = APIRouter(prefix="/api/v1/databases", tags=["databases"])


@router.get("")
def list_databases(
    page: int = 1,
    page_size: int = 20,
    user: dict = Depends(get_current_user),
):
    page_size = min(max(page_size, 1), 100)
    with get_cursor() as cur:
        cur.execute("SELECT count(*) FROM databases")
        total = cur.fetchone()["count"]

        cur.execute(
            "SELECT d.id, d.name, d.description, d.note, d.created_at, d.updated_at, "
            "cu.username AS created_by_name, uu.username AS updated_by_name, "
            "(SELECT count(*) FROM tables t WHERE t.database_id = d.id) AS table_count "
            "FROM databases d "
            "LEFT JOIN users cu ON cu.id = d.created_by "
            "LEFT JOIN users uu ON uu.id = d.updated_by "
            "ORDER BY d.id LIMIT %s OFFSET %s",
            (page_size, (page - 1) * page_size),
        )
        items = [dict(r) for r in cur.fetchall()]
    return {"code": 0, "data": {"items": items, "total": total, "page": page, "page_size": page_size}, "message": "ok"}


class CreateDBRequest(BaseModel):
    name: str
    description: str | None = None


@router.post("")
def create_database(req: CreateDBRequest, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    with get_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO databases (name, description, created_by) "
                "VALUES (%s, %s, %s) RETURNING id",
                (req.name, req.description, user["id"]),
            )
            db_id = cur.fetchone()["id"]
        except Exception:
            raise HTTPException(status_code=409, detail="库名已存在")
    return {"code": 0, "data": {"id": db_id}, "message": "ok"}


class UpdateDBRequest(BaseModel):
    note: str | None = None
    description: str | None = None


@router.put("/{db_id}")
def update_database(db_id: int, req: UpdateDBRequest, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    with get_cursor() as cur:
        sets = []
        params = []
        if req.note is not None:
            sets.append("note = %s")
            params.append(req.note)
        if req.description is not None:
            sets.append("description = %s")
            params.append(req.description)
        if sets:
            sets.append("updated_by = %s")
            params.append(user["id"])
            sets.append("updated_at = now()")
            params.append(db_id)
            cur.execute(
                f"UPDATE databases SET {', '.join(sets)} WHERE id = %s", params
            )
    return {"code": 0, "data": None, "message": "ok"}


@router.get("/{db_id}/preview-delete")
def preview_delete(db_id: int, user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    with get_cursor() as cur:
        cur.execute("SELECT count(*) FROM tables WHERE database_id = %s", (db_id,))
        table_count = cur.fetchone()["count"]
        cur.execute(
            "SELECT count(*) FROM fields f "
            "JOIN table_columns tc ON tc.field_hash = f.hash "
            "JOIN tables t ON t.id = tc.table_id "
            "WHERE t.database_id = %s",
            (db_id,),
        )
        field_count = cur.fetchone()["count"]
        cur.execute("SELECT name FROM databases WHERE id = %s", (db_id,))
        row = cur.fetchone()
        db_name = row["name"] if row else "?"
    return {
        "code": 0,
        "data": {"db_name": db_name, "tables": table_count, "fields": field_count},
        "message": "ok",
    }


@router.delete("/{db_id}")
def delete_database(db_id: int, confirm_name: str = "", user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    with get_cursor() as cur:
        cur.execute("SELECT name FROM databases WHERE id = %s", (db_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="库不存在")
        if confirm_name != row["name"]:
            raise HTTPException(status_code=400, detail=f"请输入库名确认: {row['name']}")

        # 级联删除
        cur.execute(
            "DELETE FROM field_references WHERE referenced_hash IN "
            "(SELECT f.hash FROM fields f JOIN table_columns tc ON tc.field_hash = f.hash "
            "JOIN tables t ON t.id = tc.table_id WHERE t.database_id = %s) "
            "OR referrer_hash IN (SELECT f.hash FROM fields f JOIN table_columns tc ON tc.field_hash = f.hash "
            "JOIN tables t ON t.id = tc.table_id WHERE t.database_id = %s)",
            (db_id, db_id),
        )
        cur.execute("DELETE FROM table_column_mappings WHERE table_column_id IN (SELECT id FROM table_columns WHERE table_id IN (SELECT id FROM tables WHERE database_id = %s))", (db_id,))
        cur.execute("DELETE FROM table_input_subqueries WHERE table_id IN (SELECT id FROM tables WHERE database_id = %s)", (db_id,))
        cur.execute("DELETE FROM table_columns WHERE table_id IN (SELECT id FROM tables WHERE database_id = %s)", (db_id,))
        cur.execute("DELETE FROM tables WHERE database_id = %s", (db_id,))
        cur.execute("DELETE FROM databases WHERE id = %s", (db_id,))

    return {"code": 0, "data": None, "message": "ok"}
