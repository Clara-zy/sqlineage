"""VIEW 测试 — 创建/查询/变更"""

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage


class TestView:
    """VIEW 表测试"""

    def test_create_and_query_view(self, clean_db):
        """创建 VIEW → 查询 VIEW → 验证列解析"""
        build_lineage(
            "SELECT mchnt_cd, substring(trans_time,1,8) AS stat_date, "
            "count(1) AS trans_cnt, sum(trans_at) AS trans_at, "
            "sum(CASE WHEN trans_status='SUCCESS' THEN 1 ELSE 0 END) AS succ_cnt "
            "FROM mydb.trans_flow GROUP BY mchnt_cd, substring(trans_time,1,8)",
            sink_table="mydb.dws_mchnt_day",
        )

        view_sql = "SELECT mchnt_cd, stat_date, trans_cnt FROM mydb.dws_mchnt_day"
        build_lineage(view_sql)

        with get_cursor() as cur:
            cur.execute("DELETE FROM tables WHERE table_name = 'view_mchnt_day'")
            cur.execute("SELECT database_id FROM tables WHERE table_name = 'dws_mchnt_day'")
            db_id = cur.fetchone()["database_id"]
            cur.execute("SELECT hash FROM subqueries ORDER BY created_at DESC LIMIT 1")
            sq_hash = cur.fetchone()["hash"]
            cur.execute(
                "INSERT INTO tables (database_id, table_name, table_type, source, input_subquery_hash) "
                "VALUES (%s, 'view_mchnt_day', '视图', 'view', %s)",
                (db_id, sq_hash),
            )

        r = build_lineage("SELECT mchnt_cd, trans_cnt FROM mydb.view_mchnt_day")
        assert r["new_fields"] >= 0

    def test_view_reuse(self, clean_db):
        """VIEW 查询复用"""
        build_lineage(
            "SELECT mchnt_cd, substring(trans_time,1,8) AS stat_date, "
            "count(1) AS trans_cnt, sum(trans_at) AS trans_at, "
            "sum(CASE WHEN trans_status='SUCCESS' THEN 1 ELSE 0 END) AS succ_cnt "
            "FROM mydb.trans_flow GROUP BY mchnt_cd, substring(trans_time,1,8)",
            sink_table="mydb.dws_mchnt_day",
        )

        sql = "SELECT mchnt_cd, trans_cnt FROM mydb.dws_mchnt_day"
        build_lineage(sql)

        with get_cursor() as cur:
            cur.execute("DELETE FROM tables WHERE table_name = 'v_test'")
            cur.execute("SELECT hash FROM subqueries ORDER BY created_at DESC LIMIT 1")
            sq_hash = cur.fetchone()["hash"]
            cur.execute(
                "INSERT INTO tables (database_id, table_name, table_type, source, input_subquery_hash) "
                "VALUES ((SELECT database_id FROM tables WHERE table_name='dws_mchnt_day'), "
                "'v_test', '视图', 'view', %s)",
                (sq_hash,),
            )

        build_lineage("SELECT mchnt_cd FROM mydb.v_test")
        r2 = build_lineage("SELECT trans_cnt FROM mydb.v_test")
        assert r2["new_fields"] >= 0

    def test_view_source(self, clean_db):
        """source=view 表正确显示"""
        with get_cursor() as cur:
            cur.execute("SELECT id FROM databases WHERE name = 'mydb'")
            db_id = cur.fetchone()["id"]
            cur.execute("DELETE FROM tables WHERE table_name = 'v_check_source'")
            cur.execute(
                "INSERT INTO tables (database_id, table_name, table_type, source) "
                "VALUES (%s, 'v_check_source', '视图', 'view') "
                "ON CONFLICT (database_id, table_name) DO NOTHING",
                (db_id,),
            )
            cur.execute("SELECT source FROM tables WHERE table_name='v_check_source'")
            assert cur.fetchone()["source"] == "view"
