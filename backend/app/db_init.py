"""数据库初始化模块

读取 schema.sql 并在 PostgreSQL 中创建所有表 + 默认管理员。
"""

import os
from app.database import get_connection


SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def init_database():
    """执行 schema.sql 初始化数据库"""
    conn = get_connection()
    try:
        with open(SCHEMA_PATH, "r") as f:
            sql = f.read()

        with conn.cursor() as cur:
            cur.execute(sql)

        conn.commit()

        # 统计创建的表数量
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            count = cur.fetchone()[0]

        print(f"✓ 数据库初始化完成 — {count} 张表已就绪")
        print("  默认管理员: admin / nbkknb")
    except Exception as e:
        conn.rollback()
        print(f"✗ 初始化失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_database()
    from app.seed import seed
    seed()
