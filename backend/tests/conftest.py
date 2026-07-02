"""测试 fixtures — 复用持久化 schema，只清理血缘产物"""

import pytest
from app.database import get_cursor


def ensure_seed():
    """确保种子数据存在（幂等调用）"""
    from app.seed import seed
    seed()


def cleanup_lineage():
    """清理血缘产物，保留表结构"""
    with get_cursor() as cur:
        cur.execute("DELETE FROM field_references")
        cur.execute("DELETE FROM table_column_mappings")
        cur.execute("DELETE FROM table_input_subqueries")
        cur.execute("DELETE FROM natural_semantics")
        cur.execute("DELETE FROM field_enums")
        cur.execute("DELETE FROM subqueries")
        cur.execute("DELETE FROM fields")
        # 清除 table_columns 上的冗余 field_hash 指针，避免指向已删除的 fields
        cur.execute("UPDATE table_columns SET field_hash = NULL, updated_at = now()")
        cur.execute("UPDATE tables SET input_subquery_hash = NULL, updated_at = now()")


@pytest.fixture(scope="function")
def clean_db():
    """每个测试前确保种子数据存在，清理血缘产物"""
    ensure_seed()
    cleanup_lineage()
    yield
    # 不清理表结构，保留给下一个测试
