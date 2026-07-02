"""用户管理 API — 仅 admin"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import bcrypt

from app.api.auth import get_current_user
from app.database import get_cursor

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def require_admin(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    return user


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "guest"


class UpdateUserRequest(BaseModel):
    role: str | None = None
    password: str | None = None


@router.get("")
def list_users(
    page: int = 1,
    page_size: int = 20,
    _admin: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        cur.execute("SELECT count(*) FROM users")
        total = cur.fetchone()["count"]
        cur.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id LIMIT %s OFFSET %s",
            (page_size, (page - 1) * page_size),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {"code": 0, "data": {"items": rows, "total": total, "page": page, "page_size": page_size}}


@router.post("")
def create_user(
    req: CreateUserRequest,
    _admin: dict = Depends(require_admin),
):
    hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    with get_cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
                (req.username, hashed, req.role),
            )
            user_id = cur.fetchone()["id"]
        except Exception:
            raise HTTPException(status_code=409, detail="用户名已存在")
    return {"code": 0, "data": {"id": user_id}, "message": "ok"}


@router.put("/{user_id}")
def update_user(
    user_id: int,
    req: UpdateUserRequest,
    _admin: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        if req.role:
            cur.execute("UPDATE users SET role = %s, updated_at = now() WHERE id = %s", (req.role, user_id))
        if req.password:
            hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
            cur.execute("UPDATE users SET password_hash = %s, updated_at = now() WHERE id = %s", (hashed, user_id))
    return {"code": 0, "data": None, "message": "ok"}


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    _admin: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    return {"code": 0, "data": None, "message": "ok"}
