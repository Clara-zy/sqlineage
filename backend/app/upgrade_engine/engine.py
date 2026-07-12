"""血缘追溯升级 — root→middle 级联（含 middle 表穿透）"""

import json
from collections import deque

from app.database import get_cursor
from app.lineage_engine.context import compute_hash


def preview_upgrade(table_name: str) -> dict:
    """预览升级影响范围"""
    with get_cursor() as cur:
        cur.execute("SELECT id, source FROM tables WHERE table_name = %s", (table_name,))
        row = cur.fetchone()
        if not row:
            return {"error": f"表 {table_name} 不存在"}
        if row["source"] != "root":
            return {"error": f"表 {table_name} 已是 sink"}
        table_id = row["id"]

        # 当前 field_hashes
        cur.execute(
            "SELECT field_hash FROM table_columns "
            "WHERE table_id = %s AND field_hash IS NOT NULL ORDER BY ordinal_position",
            (table_id,),
        )
        field_hashes = [r["field_hash"] for r in cur.fetchall()]

        if not field_hashes:
            return {"error": "该表无活跃字段映射"}

        # 下游字段引用
        ref_tree = _extract_tree(field_hashes)
        downstream_fields = len(ref_tree) - len(field_hashes)

        # 受影响的查询记录（通过 subquery structure 引用）
        affected_queries = _count_affected_queries(field_hashes)

        # 受影响的 middle 表（table_column_mappings 引用这些 hash）
        affected_middle = _count_affected_middle_tables(field_hashes)

        return {
            "table_name": table_name,
            "source": row["source"],
            "field_count": len(field_hashes),
            "downstream_fields": downstream_fields,
            "total_affected_nodes": len(ref_tree),
            "affected_queries": affected_queries,
            "affected_middle_tables": affected_middle,
            "sample_hashes": field_hashes[:3],
        }


def _extract_tree(roots: list[str]) -> dict[str, str]:
    tree = {}
    queue = deque(roots)
    visited = set()
    while queue:
        h = queue.popleft()
        if h in visited:
            continue
        visited.add(h)
        tree[h] = h
        with get_cursor() as cur:
            cur.execute(
                "SELECT referrer_hash FROM field_references WHERE referenced_hash = %s",
                (h,),
            )
            for row in cur.fetchall():
                ref = row["referrer_hash"]
                if ref not in visited:
                    queue.append(ref)
    return tree


def _count_affected_queries(hashes: list[str]) -> int:
    with get_cursor() as cur:
        count = 0
        for h in hashes:
            cur.execute(
                "SELECT count(*) FROM subqueries WHERE structure::text LIKE %s",
                (f"%{h}%",),
            )
            count += cur.fetchone()["count"]
        return count


def _count_affected_middle_tables(hashes: list[str]) -> list[dict]:
    with get_cursor() as cur:
        result = []
        for h in hashes:
            cur.execute(
                "SELECT tc.table_id, t.table_name, count(*) "
                "FROM table_column_mappings tcm "
                "JOIN table_columns tc ON tc.id = tcm.table_column_id "
                "JOIN tables t ON t.id = tc.table_id "
                "WHERE tcm.field_hash = %s AND t.source = 'sink' "
                "AND tcm.status = 'active' "
                "GROUP BY tc.table_id, t.table_name",
                (h,),
            )
            for row in cur.fetchall():
                result.append(
                    {"table_name": row["table_name"], "count": row["count"]}
                )
        return result


def cascade_from_sink(old_to_new: dict[str, str], table_name: str):
    """sink 变更触发的下游级联"""
    if not old_to_new:
        return

    # 1. 提取下游引用树
    ref_tree = _extract_tree(list(old_to_new.keys()))

    # 2. 字段级联
    old_to_new = _cascade_fields(old_to_new, ref_tree)

    # 3. middle 表穿透
    _cascade_middle_mappings(old_to_new)

    # 4. 子查询更新
    _cascade_subqueries(old_to_new)


def execute_upgrade(table_name: str, extended_sql: str) -> dict:
    """root→middle 升级: 改 source + sink 写入（级联由 _bind_sink_table 自动触发）"""
    from app.lineage_engine.engine import build_lineage

    with get_cursor() as cur:
        cur.execute("SELECT id, source FROM tables WHERE table_name = %s", (table_name,))
        row = cur.fetchone()
        if not row or row["source"] != "root":
            return {"error": "表不存在或已是 sink"}
        table_id = row["id"]

    # 改 source → middle
    with get_cursor() as cur:
        cur.execute("UPDATE tables SET source = 'sink' WHERE id = %s", (table_id,))

    # sink 写入（内部自动触发级联）
    result = build_lineage(extended_sql, sink_table=table_name)

    return {
        "upgraded_table": table_name,
        "new_fields": result["new_fields"],
        "new_subqueries": result["new_subqueries"],
        "message": "升级完成，级联由 sink 绑定自动触发",
    }


def _cascade_fields(
    old_to_new: dict[str, str], ref_tree: dict[str, str]
) -> dict[str, str]:
    for old_h in ref_tree:
        if old_h in old_to_new and old_to_new[old_h] != old_h:
            continue

        with get_cursor() as cur:
            cur.execute(
                "SELECT field_type, expression FROM fields WHERE hash = %s", (old_h,)
            )
            row = cur.fetchone()
            if not row:
                continue
            expr = row["expression"]
            if isinstance(expr, str):
                expr = json.loads(expr)

        new_expr = _replace_hashes(expr, old_to_new)
        new_h = compute_hash(new_expr)

        if new_h == old_h:
            continue

        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO fields (hash, field_type, expression) "
                "VALUES (%s, %s, %s) ON CONFLICT (hash) DO NOTHING",
                (new_h, row["field_type"], json.dumps(new_expr, ensure_ascii=False)),
            )

        sub_hashes = _extract_sub_hashes(new_expr)
        with get_cursor() as cur:
            for sh in sub_hashes:
                cur.execute(
                    "INSERT INTO field_references (referenced_hash, referrer_hash) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (sh, new_h),
                )

        old_to_new[old_h] = new_h
        _migrate_semantics(old_h, new_h)
        _migrate_enums(old_h, new_h)

    return old_to_new


def _cascade_middle_mappings(old_to_new: dict[str, str]):
    """穿透 middle 表：更新 table_column_mappings 中的 hash"""
    for old_h, new_h in old_to_new.items():
        if old_h == new_h:
            continue
        with get_cursor() as cur:
            # 废弃旧
            cur.execute(
                "UPDATE table_column_mappings SET status = 'deprecated', "
                "deprecated_at = now(), deprecated_reason = 'root→middle 级联' "
                "WHERE field_hash = %s AND status = 'active'",
                (old_h,),
            )
            # 插入新
            cur.execute(
                "INSERT INTO table_column_mappings (table_column_id, field_hash, status) "
                "SELECT tc.id, %s, 'active' FROM table_columns tc "
                "JOIN table_column_mappings tcm ON tcm.table_column_id = tc.id "
                "WHERE tcm.field_hash = %s AND tcm.status = 'deprecated' "
                "AND tcm.deprecated_reason = 'root→middle 级联'",
                (new_h, old_h),
            )
            # 更新 table_columns 冗余指针
            cur.execute(
                "UPDATE table_columns SET field_hash = %s, updated_at = now() "
                "WHERE id IN ("
                "  SELECT tc.id FROM table_columns tc "
                "  JOIN table_column_mappings tcm ON tcm.table_column_id = tc.id "
                "  WHERE tcm.field_hash = %s AND tcm.status = 'active'"
                ")",
                (new_h, new_h),
            )


def _cascade_subqueries(old_to_new: dict[str, str]):
    """更新子查询 structure 中的 hash 引用"""
    for old_h, new_h in old_to_new.items():
        if old_h == new_h:
            continue
        with get_cursor() as cur:
            cur.execute(
                "SELECT hash, structure FROM subqueries WHERE structure::text LIKE %s",
                (f"%{old_h}%",),
            )
            for row in cur.fetchall():
                new_struct = _replace_hashes(row["structure"], old_to_new)
                new_sq_hash = compute_hash(new_struct)
                if new_sq_hash != row["hash"]:
                    cur.execute(
                        "INSERT INTO subqueries (hash, subquery_type, structure) "
                        "VALUES (%s, 'select', %s) ON CONFLICT (hash) DO NOTHING",
                        (new_sq_hash, json.dumps(new_struct, ensure_ascii=False)),
                    )


def _replace_hashes(expr, mapping: dict):
    if isinstance(expr, dict):
        return {
            k: mapping.get(v, v) if k == "hash" and isinstance(v, str) and len(v) == 16
            else _replace_hashes(v, mapping)
            for k, v in expr.items()
        }
    elif isinstance(expr, list):
        return [_replace_hashes(item, mapping) for item in expr]
    return expr


def _extract_sub_hashes(expr) -> list[str]:
    hashes = []
    if isinstance(expr, dict):
        for k, v in expr.items():
            if k == "hash" and isinstance(v, str) and len(v) == 16:
                hashes.append(v)
            else:
                hashes.extend(_extract_sub_hashes(v))
    elif isinstance(expr, list):
        for item in expr:
            hashes.extend(_extract_sub_hashes(item))
    return hashes


def _migrate_semantics(old_h: str, new_h: str):
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, semantics, source FROM natural_semantics "
            "WHERE citizen_hash = %s AND status = 'active' AND citizen_type = 'field'",
            (old_h,),
        )
        row = cur.fetchone()
        if not row:
            return
        cur.execute(
            "UPDATE natural_semantics SET status = 'deprecated', "
            "deprecated_at = now(), deprecated_reason = 'root→middle 升级' WHERE id = %s",
            (row["id"],),
        )
        cur.execute(
            "INSERT INTO natural_semantics "
            "(citizen_hash, citizen_type, semantics, source, status) "
            "VALUES (%s, 'field', %s, %s, 'active')",
            (new_h, row["semantics"], row["source"]),
        )


def _migrate_enums(old_h: str, new_h: str):
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, enum_values, source FROM field_enums "
            "WHERE field_hash = %s AND status = 'active'",
            (old_h,),
        )
        row = cur.fetchone()
        if not row:
            return
        cur.execute(
            "UPDATE field_enums SET status = 'deprecated', "
            "deprecated_at = now(), deprecated_reason = 'root→middle 升级' WHERE id = %s",
            (row["id"],),
        )
        cur.execute(
            "INSERT INTO field_enums (field_hash, enum_values, source, status) "
            "VALUES (%s, %s, %s, 'active')",
            (new_h, json.dumps(row["enum_values"], ensure_ascii=False), row["source"]),
        )
