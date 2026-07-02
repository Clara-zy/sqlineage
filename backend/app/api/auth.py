"""认证 API — 登录 / 登出 / 当前用户"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
import bcrypt

from app.database import get_cursor
from app.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    role: str


class LoginResponse(BaseModel):
    token: str
    user: UserResponse
    expires_at: str


SESSION_TTL_HOURS = 24


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_session(user_id: int) -> tuple[str, datetime]:
    token = uuid.uuid4().hex
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (token, user_id, expires),
        )
    return token, expires


def get_current_user(authorization: str = Header(...)) -> dict:
    """从 Authorization header 提取并校验当前用户"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供有效的认证令牌")
    token = authorization[7:]
    with get_cursor() as cur:
        cur.execute(
            """SELECT u.id, u.username, u.role
               FROM sessions s JOIN users u ON s.user_id = u.id
               WHERE s.token = %s AND s.expires_at > now()""",
            (token,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="令牌无效或已过期")
        return dict(row)


@router.post("/login")
def login(req: LoginRequest):
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = %s",
            (req.username,),
        )
        row = cur.fetchone()
        if not row or not verify_password(req.password, row["password_hash"]):
            return {"code": 40101, "data": None, "message": "用户名或密码错误"}

        token, expires = create_session(row["id"])
        return {
            "code": 0,
            "data": {
                "token": token,
                "user": {
                    "id": row["id"],
                    "username": row["username"],
                    "role": row["role"],
                },
                "expires_at": expires.isoformat(),
            },
            "message": "ok",
        }


@router.post("/logout")
def logout(authorization: str = Header(...)):
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    with get_cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
    return {"code": 0, "data": None, "message": "ok"}


@router.get("/me")
def me(user: dict = __import__("fastapi").Depends(get_current_user)):
    return {
        "code": 0,
        "data": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
        },
        "message": "ok",
    }
