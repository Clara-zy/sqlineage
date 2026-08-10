"""清除血缘数据（保留表结构 + 种子数据）"""
from app.database import get_cursor

with get_cursor() as cur:
    cur.execute("DELETE FROM field_references")
    cur.execute("DELETE FROM table_column_mappings")
    cur.execute("DELETE FROM table_input_subqueries")
    cur.execute("DELETE FROM natural_semantics")
    cur.execute("DELETE FROM build_history")
    cur.execute("DELETE FROM subqueries")
    cur.execute("DELETE FROM fields")
    cur.execute("UPDATE table_columns SET field_hash = NULL, updated_at = now()")
    cur.execute("UPDATE tables SET input_subquery_hash = NULL, updated_at = now()")
print("Lineage data cleared.")
