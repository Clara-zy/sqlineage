"""测例导入脚本 — 读取 test_sqls.json 批量调用 build_lineage 入库

用法:
    make import-tests
    或
    env PYTHONPATH=backend backend/.venv/bin/python backend/app/import_tests.py
"""

import json
import os
import sys
import traceback

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.lineage_engine.validators import ValidationError
from app.seed import seed

MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "resource", "test_sqls.json"
)


def main():
    # 确保种子数据存在
    seed()

    # 清空旧的构建历史
    with get_cursor() as cur:
        cur.execute("DELETE FROM build_history")

    manifest_path = os.path.abspath(MANIFEST_PATH)
    if not os.path.exists(manifest_path):
        print(f"✗ 清单文件不存在: {manifest_path}")
        sys.exit(1)

    with open(manifest_path, "r") as f:
        cases = json.load(f)

    success = 0
    skipped = 0
    failed = 0

    for case in cases:
        cid = case["id"]
        title = case["title"]
        sql = case["sql"]
        comment = f"[{cid}] {title}"
        sink_table = case.get("sink_table")

        # VIEW 创建（特殊处理）
        view_create = case.get("view_create")
        if view_create:
            try:
                _create_view(view_create)
            except Exception as e:
                print(f"  ⚠ VIEW 创建失败 ({cid}): {e}")

        try:
            result = build_lineage(sql, sink_table=sink_table, comment=comment)
            new_f = result.get("new_fields", 0)
            new_sq = result.get("new_subqueries", 0)
            print(f"  ✓ {cid}  {title}")
            print(f"        新建字段 {new_f} / 子查询 {new_sq}")
            success += 1
        except ValidationError as e:
            print(f"  ⊘ {cid}  {title}  — 校验拒绝 [{e.code}] {e.message}")
            skipped += 1
        except Exception as e:
            print(f"  ✗ {cid}  {title}  — {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"总计: {len(cases)}  成功: {success}  跳过: {skipped}  失败: {failed}")


def _create_view(ddl: str):
    """从 DDL 创建 VIEW 表结构（支持 CREATE VIEW ... AS SELECT ...）"""
    import re

    # CREATE VIEW name AS SELECT col1, col2 FROM ...
    m = re.search(
        r"CREATE\s+VIEW\s+(\w+)\s+AS\s+SELECT\s+(.+?)\s+FROM\s+\w+",
        ddl, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        raise ValueError(f"VIEW DDL 解析失败: {ddl[:60]}")

    table_name = m.group(1)
    cols_text = m.group(2)

    # 解析列名（简易按逗号分隔）
    columns = []
    for part in cols_text.split(","):
        part = part.strip()
        # 去掉表前缀如 dim_mchnt.
        col_name = part.split(".")[-1].strip()
        if col_name and col_name != "*":
            columns.append((col_name, "STRING", ""))

    if not columns:
        raise ValueError(f"VIEW 列解析为空: {cols_text}")

    db_name = "mydb"

    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO databases (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (db_name,),
        )
        cur.execute("SELECT id FROM databases WHERE name = %s", (db_name,))
        db_id = cur.fetchone()["id"]

        cur.execute(
            "INSERT INTO tables (database_id, table_name, table_type, source) "
            "VALUES (%s, %s, '视图', 'view') "
            "ON CONFLICT (database_id, table_name) DO NOTHING RETURNING id",
            (db_id, table_name),
        )
        row = cur.fetchone()
        if row:
            table_id = row["id"]
            for i, (col_name, col_type, col_comment) in enumerate(columns, 1):
                cur.execute(
                    "INSERT INTO table_columns "
                    "(table_id, column_name, column_type, column_comment, ordinal_position) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (table_id, column_name) DO NOTHING",
                    (table_id, col_name, col_type, col_comment, i),
                )


if __name__ == "__main__":
    main()
