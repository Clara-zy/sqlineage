"""Layer 5-8 测试 — 组合条件 / UNION / 窗口函数 / CTE"""

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage


class TestLayer5:
    """复杂组合条件 — AND/OR/IN/NOT IN/IS NOT NULL"""

    def test_and_or_condition(self, clean_db):
        """复合 AND + OR 条件"""
        result = build_lineage("""
            SELECT sum(trans_at) as trans_at, count(*) AS cnt
            FROM trans_flow
            WHERE trans_status = 'SUCCESS'
              AND (trans_at > 500 OR mchnt_cd = 'M001')
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM fields WHERE field_type = 'composite_condition'"
            )
            assert cur.fetchone()["count"] >= 1  # AND 节点

    def test_in_not_in_condition(self, clean_db):
        """IN + NOT IN 条件"""
        result = build_lineage("""
            SELECT count(*) AS cnt
            FROM trans_flow
            WHERE trans_status IN ('SUCCESS', 'REFUND')
              AND mchnt_cd NOT IN ('M9999')
        """)
        assert result["new_fields"] > 0

    def test_is_not_null(self, clean_db):
        """IS NOT NULL"""
        result = build_lineage("""
            SELECT count(*) AS cnt
            FROM trans_flow
            WHERE trans_at IS NOT NULL
        """)
        assert result["new_fields"] > 0

    def test_complex_nested_condition(self, clean_db):
        """Q4 风格的嵌套组合条件"""
        result = build_lineage("""
            SELECT count(DISTINCT usr_id) AS cnt
            FROM usr_behave_bury bu
            WHERE bu.behave_time >= '2026-06-01'
              AND (
                  bu.behave_type = 'CLICK'
                  OR (
                      bu.behave_type IN ('VIEW', 'SEARCH')
                      AND bu.mchnt_cd IS NOT NULL
                  )
              )
              AND bu.mchnt_cd NOT IN ('M9999')
        """)
        assert result["new_fields"] > 0


class TestLayer6:
    """UNION"""

    def test_union_all(self, clean_db):
        """Q9: UNION ALL"""
        # 先灌 middle 表数据
        _seed_dws(clean_db)

        result = build_lineage("""
            SELECT mchnt_cd, 'day' AS stat_level, stat_date AS stat_time, trans_cnt, trans_at
            FROM dws_mchnt_day
            WHERE stat_date >= '20260601'
            UNION ALL
            SELECT mchnt_cd, 'month' AS stat_level, substring(stat_date, 1, 6) AS stat_time,
                   sum(trans_cnt) AS trans_cnt, sum(trans_at) AS trans_at
            FROM dws_mchnt_day
            WHERE stat_date >= '20260601'
            GROUP BY mchnt_cd, substring(stat_date, 1, 6)
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM subqueries WHERE subquery_type = 'union'"
            )
            assert cur.fetchone()["count"] >= 1

    def test_simple_union(self, clean_db):
        """简单 UNION ALL"""
        result = build_lineage("""
            SELECT trans_id FROM trans_flow WHERE trans_status = 'SUCCESS'
            UNION ALL
            SELECT trans_id FROM trans_flow WHERE trans_status = 'REFUND'
        """)
        assert result["new_fields"] > 0


class TestLayer7:
    """窗口函数"""

    def test_window_row_number(self, clean_db):
        """row_number() OVER (PARTITION BY ... ORDER BY ...)"""
        result = build_lineage("""
            SELECT mchnt_cd, trans_id,
                   row_number() OVER (PARTITION BY mchnt_cd ORDER BY trans_time) AS rn
            FROM trans_flow
        """)
        assert result["new_fields"] > 0

    def test_window_cumsum(self, clean_db):
        """Q10: 累计求和窗口"""
        result = build_lineage("""
            SELECT mchnt_cd, trans_id, trans_at, trans_time,
                   sum(trans_at) OVER (
                       PARTITION BY mchnt_cd
                       ORDER BY trans_time
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cumsum_at,
                   row_number() OVER (
                       PARTITION BY mchnt_cd
                       ORDER BY trans_time
                   ) AS rn
            FROM trans_flow
            WHERE trans_status = 'SUCCESS'
        """)
        assert result["new_fields"] > 0


class TestLayer8:
    """CTE (WITH)"""

    def test_simple_cte(self, clean_db):
        """Q11: WITH ... SELECT"""
        result = build_lineage("""
            WITH succ_trans AS (
                SELECT mchnt_cd, trans_at, trans_time
                FROM trans_flow
                WHERE trans_status = 'SUCCESS'
            )
            SELECT mchnt_cd, sum(trans_at) AS total_at, count(1) AS cnt
            FROM succ_trans
            GROUP BY mchnt_cd
        """)
        assert result["new_fields"] > 0

    def test_multi_cte(self, clean_db):
        """多个 CTE + 关联查询"""
        result = build_lineage("""
            WITH succ AS (
                SELECT mchnt_cd, trans_at, trans_time
                FROM trans_flow WHERE trans_status = 'SUCCESS'
            ),
            stats AS (
                SELECT mchnt_cd, sum(trans_at) AS total_at, count(1) AS cnt
                FROM succ GROUP BY mchnt_cd
            )
            SELECT a.mchnt_cd, b.mchnt_nm, a.total_at, a.cnt
            FROM stats a
            LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
        """)
        assert result["new_fields"] > 0


def _seed_dws(clean_db):
    """为 middle 表 dws_mchnt_day 创建血缘数据"""
    build_lineage("""
        SELECT mchnt_cd,
               date_format(trans_time, 'yyyyMMdd') AS stat_date,
               count(1) AS trans_cnt,
               sum(trans_at) AS trans_at,
               sum(CASE WHEN trans_status = 'SUCCESS' THEN 1 ELSE 0 END) AS succ_cnt
        FROM trans_flow
        GROUP BY mchnt_cd, date_format(trans_time, 'yyyyMMdd')
    """, sink_table="dws_mchnt_day")
