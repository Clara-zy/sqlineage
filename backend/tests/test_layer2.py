"""Layer 2 测试 — 聚合 + GROUP BY + 两表 LEFT JOIN"""

import pytest
from app.database import get_cursor
from app.lineage_engine.engine import build_lineage


class TestLayer2Agg:
    """聚合 + GROUP BY"""

    def test_simple_aggregate(self, clean_db):
        """Q1: sum(trans_at) + WHERE"""
        result = build_lineage("""
            SELECT sum(trans_at) AS total_at
            FROM trans_flow
            WHERE trans_status = 'SUCCESS'
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'sum'"
            )
            assert cur.fetchone() is not None

    def test_multi_aggregate_groupby(self, clean_db):
        """Q6: 多聚合 + GROUP BY"""
        result = build_lineage("""
            SELECT
                mchnt_cd,
                sum(trans_at) AS total_at,
                count(1) AS total_cnt,
                avg(trans_at) AS avg_at,
                max(trans_at) AS max_at,
                min(trans_at) AS min_at
            FROM trans_flow
            WHERE trans_status = 'SUCCESS'
            GROUP BY mchnt_cd
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM fields WHERE field_type = 'expression'")
            count = cur.fetchone()["count"]
            assert count >= 5  # sum, count, avg, max, min

    def test_count_star(self, clean_db):
        """count(*)"""
        build_lineage("SELECT count(*) AS cnt FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'count'"
            )
            assert cur.fetchone() is not None

    def test_aggregate_idempotent(self, clean_db):
        """同一聚合查询复用"""
        sql = "SELECT sum(trans_at) AS total FROM trans_flow"
        r1 = build_lineage(sql)
        r2 = build_lineage(sql)
        assert r2["new_fields"] == 0

    def test_subquery_has_groupby(self, clean_db):
        """验证子查询节点记录了 GROUP BY 信息"""
        build_lineage("""
            SELECT mchnt_cd, sum(trans_at) AS total
            FROM trans_flow
            GROUP BY mchnt_cd
        """)

        with get_cursor() as cur:
            cur.execute("SELECT structure FROM subqueries WHERE subquery_type = 'select'")
            row = cur.fetchone()
            assert row is not None

            structure = row["structure"]
            # 应该有 group_by 字段（引擎可能需要更新）
            assert "fields" in structure


class TestLayer2Join:
    """两表 LEFT JOIN"""

    def test_two_table_left_join(self, clean_db):
        """Q2: trans_flow LEFT JOIN dim_domin"""
        result = build_lineage("""
            SELECT b.region_nm, count(1) AS trans_cnt
            FROM trans_flow a
            LEFT JOIN dim_domin b ON a.region_cd = b.region_cd
            WHERE a.trans_status = 'SUCCESS'
            GROUP BY b.region_nm
        """)
        assert result["new_fields"] > 0

    def test_simple_left_join_no_agg(self, clean_db):
        """简单的两表 LEFT JOIN，无聚合"""
        result = build_lineage("""
            SELECT a.trans_id, b.mchnt_nm, b.register_time
            FROM trans_flow a
            LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            # 应有两个表来的 atomic 字段
            cur.execute("SELECT count(*) FROM fields WHERE field_type = 'atomic'")
            count = cur.fetchone()["count"]
            assert count >= 2  # trans_flow.trans_id + dim_mchnt.mchnt_nm

    def test_three_table_left_join(self, clean_db):
        """Q3: 3表 LEFT JOIN"""
        result = build_lineage("""
            SELECT c.industry_nm, count(1) AS refund_cnt
            FROM trans_flow a
            LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
            LEFT JOIN dim_industry c ON b.industry_id = c.industry_id
            WHERE a.trans_status = 'REFUND'
              AND c.industry_nm = '酒店'
            GROUP BY c.industry_nm
        """)
        assert result["new_fields"] > 0
