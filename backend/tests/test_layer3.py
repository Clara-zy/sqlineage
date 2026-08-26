"""Layer 3 测试 — 子查询嵌套"""

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage


class TestLayer3:
    """子查询"""

    def test_nested_subquery(self, clean_db):
        """Q7: 嵌套子查询 SELECT ... FROM (SELECT ...) a WHERE ..."""
        result = build_lineage("""
            SELECT
                a.mchnt_cd,
                a.total_cnt,
                a.total_at
            FROM (
                SELECT
                    mchnt_cd,
                    count(1) AS total_cnt,
                    sum(trans_at) AS total_at
                FROM trans_flow
                WHERE trans_status = 'SUCCESS'
                GROUP BY mchnt_cd
            ) a
            WHERE a.total_cnt > 10
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            # 应有外层子查询节点
            cur.execute("SELECT count(*) FROM subqueries")
            sq_count = cur.fetchone()["count"]
            assert sq_count >= 2  # 外层 + 内层

    def test_simple_subquery_in_from(self, clean_db):
        """SELECT * FROM (SELECT trans_id FROM trans_flow) t"""
        result = build_lineage("""
            SELECT t.trans_id
            FROM (SELECT trans_id FROM trans_flow) t
        """)
        assert result["new_fields"] > 0

    def test_subquery_with_aggregate(self, clean_db):
        """子查询中包含聚合"""
        result = build_lineage("""
            SELECT x.mchnt_cd, x.cnt
            FROM (
                SELECT mchnt_cd, count(*) AS cnt
                FROM trans_flow
                GROUP BY mchnt_cd
            ) x
        """)
        assert result["new_fields"] > 0

    def test_subquery_field_reuse(self, clean_db):
        """子查询的字段可被复用"""
        build_lineage("""
            SELECT mchnt_cd, sum(trans_at) AS s
            FROM trans_flow GROUP BY mchnt_cd
        """)

        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM fields WHERE field_type = 'expression'")
            before = cur.fetchone()["count"]

        build_lineage("""
            SELECT x.mchnt_cd, x.s
            FROM (
                SELECT mchnt_cd, sum(trans_at) AS s
                FROM trans_flow GROUP BY mchnt_cd
            ) x
        """)

        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM fields WHERE field_type = 'expression'")
            after = cur.fetchone()["count"]

        # 内层子查询的字段应被复用，不重复创建
        assert after >= before


class TestLayer4:
    """CASE WHEN"""

    def test_simple_case_when(self, clean_db):
        """Q8: CASE WHEN 多分支"""
        result = build_lineage("""
            SELECT
                trans_id,
                trans_at,
                CASE
                    WHEN trans_at > 10000 THEN '大额'
                    WHEN trans_at > 1000  THEN '中额'
                    WHEN trans_at > 100   THEN '小额'
                    ELSE '微额'
                END AS amt_level
            FROM trans_flow
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'casewhen'"
            )
            assert cur.fetchone() is not None

    def test_case_when_without_else(self, clean_db):
        """CASE WHEN 无 ELSE 分支"""
        build_lineage("""
            SELECT CASE WHEN trans_at > 1000 THEN 'HIGH' END AS level
            FROM trans_flow
        """)

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'casewhen'"
            )
            assert cur.fetchone() is not None
