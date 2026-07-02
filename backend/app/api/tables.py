"""表结构 API — 暂存占位"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/tables", tags=["tables"])


@router.get("")
def list_tables():
    return {"code": 0, "data": {"items": [], "total": 0}}


@router.post("")
def create_table():
    return {"code": 0, "data": None, "message": "not implemented"}
