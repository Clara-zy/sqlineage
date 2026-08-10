"""血缘构建上下文 — 会话级缓存与数据库访问"""

import hashlib
import json

from app.database import get_cursor


def compute_hash(expression: dict) -> str:
    """计算字段/子查询的内容寻址 hash"""
    canonical = json.dumps(expression, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class BuildContext:
    """血缘构建上下文，管理缓存和数据库会话"""

    def __init__(self):
        self.cache: dict[str, str] = {}  # canonical_json → field_hash
        self.alias_map: dict[str, str] = {}  # 表别名 → 真实表名
        self.subquery_map: dict[str, dict] = {}  # 子查询别名 → {ast, projection_hashes}
        self.join_wraps: dict[str, list[str]] = {}  # 别名 → [on_hash, ...] 栈式包裹
        self.from_alias: str | None = None  # FROM 表别名（star 节点继承其 JOIN+WHERE 包裹）
        self.group_keys: list[str] = []  # 当前子查询的 GROUP BY 字段 hash 列表
        self.pending_where: str | None = None  # 当前子查询的 WHERE hash，列引用时应用
        self.pending_having: str | None = None  # 当前子查询的 HAVING hash，聚合后应用
        self.inner_subqueries: list[str] = []  # 当前子查询内嵌的子查询 hash（FROM 子查询）
        self.projection_map: dict[str, str] = {}  # 投影列名 → field hash（ORDER BY 别名解析用）

    # ── 表结构查询 ──

    def get_table(self, db_name: str, table_name: str) -> dict | None:
        with get_cursor() as cur:
            if db_name:
                cur.execute(
                    "SELECT t.id, d.name AS db_name, t.table_name, t.source, "
                    "t.input_subquery_hash "
                    "FROM tables t JOIN databases d ON d.id = t.database_id "
                    "WHERE d.name = %s AND t.table_name = %s",
                    (db_name, table_name),
                )
            else:
                cur.execute(
                    "SELECT t.id, d.name AS db_name, t.table_name, t.source, "
                    "t.input_subquery_hash "
                    "FROM tables t JOIN databases d ON d.id = t.database_id "
                    "WHERE t.table_name = %s",
                    (table_name,),
                )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_table_columns(self, table_id: int) -> list[dict]:
        with get_cursor() as cur:
            cur.execute(
                "SELECT id, column_name, column_type, column_comment, "
                "ordinal_position, field_hash "
                "FROM table_columns WHERE table_id = %s ORDER BY ordinal_position",
                (table_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_table_column_by_name(self, table_id: int, column_name: str) -> dict | None:
        with get_cursor() as cur:
            cur.execute(
                "SELECT id, field_hash FROM table_columns "
                "WHERE table_id = %s AND column_name = %s",
                (table_id, column_name),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    # ── 字段操作 ──

    def upsert_field(self, hash_val: str, field_type: str, expression: dict) -> bool:
        """返回 True 表示新建，False 表示已存在"""
        with get_cursor() as cur:
            cur.execute("SELECT 1 FROM fields WHERE hash = %s", (hash_val,))
            if cur.fetchone():
                return False
            cur.execute(
                "INSERT INTO fields (hash, field_type, expression) VALUES (%s, %s, %s)",
                (hash_val, field_type, json.dumps(expression, ensure_ascii=False)),
            )
            return True

    def upsert_field_reference(self, referenced: str, referrer: str):
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO field_references (referenced_hash, referrer_hash) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (referenced, referrer),
            )

    # ── 子查询操作 ──

    def upsert_subquery(self, hash_val: str, sq_type: str, structure: dict) -> bool:
        with get_cursor() as cur:
            cur.execute("SELECT 1 FROM subqueries WHERE hash = %s", (hash_val,))
            if cur.fetchone():
                return False
            cur.execute(
                "INSERT INTO subqueries (hash, subquery_type, structure) VALUES (%s, %s, %s)",
                (hash_val, sq_type, json.dumps(structure, ensure_ascii=False)),
            )
            return True

    # ── 表-字段映射 ──

    def update_table_column_field_hash(self, column_id_or_table_id: int, field_hash_or_col_name: str, hash_override: str | None = None):
        with get_cursor() as cur:
            if hash_override is not None:
                # 新签名: (table_id, column_name, field_hash)
                cur.execute(
                    "UPDATE table_columns SET field_hash = %s, updated_at = now() "
                    "WHERE table_id = %s AND column_name = %s",
                    (hash_override, column_id_or_table_id, field_hash_or_col_name),
                )
            else:
                # 旧签名: (column_id, field_hash)
                cur.execute(
                    "UPDATE table_columns SET field_hash = %s, updated_at = now() WHERE id = %s",
                    (field_hash_or_col_name, column_id_or_table_id),
                )

    # ── 追踪表操作 ──

    def deprecate_active_mappings(self, table_column_id: int, reason: str):
        with get_cursor() as cur:
            cur.execute(
                "UPDATE table_column_mappings SET status = 'deprecated', "
                "deprecated_at = now(), deprecated_reason = %s "
                "WHERE table_column_id = %s AND status = 'active'",
                (reason, table_column_id),
            )

    def insert_column_mapping(self, table_column_id: int, field_hash: str):
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO table_column_mappings (table_column_id, field_hash, status) "
                "VALUES (%s, %s, 'active')",
                (table_column_id, field_hash),
            )

    def deprecate_active_input_subquery(self, table_id: int, reason: str):
        with get_cursor() as cur:
            cur.execute(
                "UPDATE table_input_subqueries SET status = 'deprecated', "
                "deprecated_at = now(), deprecated_reason = %s "
                "WHERE table_id = %s AND status = 'active'",
                (reason, table_id),
            )

    def insert_input_subquery(self, table_id: int, subquery_hash: str):
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO table_input_subqueries (table_id, subquery_hash, status) "
                "VALUES (%s, %s, 'active')",
                (table_id, subquery_hash),
            )

    def update_table_input_subquery(self, table_id: int, subquery_hash: str):
        with get_cursor() as cur:
            cur.execute(
                "UPDATE tables SET input_subquery_hash = %s, updated_at = now() WHERE id = %s",
                (subquery_hash, table_id),
            )
