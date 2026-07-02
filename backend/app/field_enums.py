"""字段枚举值管理 — 解析 DDL COMMENT / 继承 / 校验"""

import re
from app.database import get_cursor

ENUM_PATTERN = re.compile(r"ENUM\(([^)]+)\)")


def parse_enum_from_comment(comment: str) -> list[str] | None:
    """从 COMMENT 中提取 ENUM(...) 值列表"""
    if not comment:
        return None
    m = ENUM_PATTERN.search(comment)
    if not m:
        return None
    return [v.strip() for v in m.group(1).split(",") if v.strip()]


def upsert_field_enum(field_hash: str, values: list[str], source: str = "ddl_comment"):
    """写入或更新字段的枚举值"""
    with get_cursor() as cur:
        # 弃用旧记录
        cur.execute(
            "UPDATE field_enums SET status = 'deprecated', "
            "deprecated_at = now(), deprecated_reason = '新枚举值覆盖' "
            "WHERE field_hash = %s AND status = 'active'",
            (field_hash,),
        )
        # 写入新记录
        import json
        cur.execute(
            "INSERT INTO field_enums (field_hash, enum_values, source, status) "
            "VALUES (%s, %s, %s, 'active')",
            (field_hash, json.dumps(values, ensure_ascii=False), source),
        )


def get_active_enum(field_hash: str) -> list[str] | None:
    """获取字段当前活跃的枚举值"""
    with get_cursor() as cur:
        cur.execute(
            "SELECT enum_values FROM field_enums "
            "WHERE field_hash = %s AND status = 'active'",
            (field_hash,),
        )
        row = cur.fetchone()
        return row["enum_values"] if row else None


def inherit_enum( parent_hash: str, child_hash: str):
    """子节点继承父节点的枚举值"""
    values = get_active_enum(parent_hash)
    if values:
        upsert_field_enum(child_hash, values, source="ddl_comment")


def validate_enum_value(field_hash: str, literal_value: str) -> str | None:
    """
    校验字面量是否在枚举范围中。
    返回警告信息，或 None（校验通过/无枚举定义）。
    """
    values = get_active_enum(field_hash)
    if not values:
        return None
    if literal_value not in values:
        return (
            f"字段 {field_hash[:8]} 枚举值 {values} 不包含字面量 '{literal_value}'"
        )
    return None
