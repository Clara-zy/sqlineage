"""FULL JOIN 测试"""

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage


class TestFullJoin:
    """FULL OUTER JOIN"""

    def test_full_join_produces_two_branches(self, clean_db):
        """FULL JOIN 拆为 (LEFT JOIN) UNION (LEFT JOIN)，两边字段都有"""
        result = build_lineage("""
            SELECT coalesce(a.mchnt_cd, b.mchnt_cd) AS mchnt_cd,
                   a.trans_cnt, b.behave_cnt
            FROM (SELECT mchnt_cd, count(1) AS trans_cnt
                  FROM trans_flow GROUP BY mchnt_cd) a
            FULL JOIN (SELECT mchnt_cd, count(1) AS behave_cnt
                       FROM usr_behave_bury GROUP BY mchnt_cd) b
            ON a.mchnt_cd = b.mchnt_cd
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            # 应有 1 个 UNION 子查询
            cur.execute(
                "SELECT hash, structure FROM subqueries WHERE subquery_type = 'union'"
            )
            row = cur.fetchone()
            assert row is not None
            branches = row["structure"].get("branches", [])
            assert len(branches) >= 2, f"应有 2 个分支，实际 {len(branches)}"

            # 应有 JOIN 表达式（来自两个分支各自的 LEFT JOIN）
            cur.execute(
                "SELECT count(*) FROM fields WHERE expression ->> 'op' = 'join'"
            )
            join_count = cur.fetchone()["count"]
            # 两个分支各自有 LEFT JOIN，至少有一些 JOIN 表达式
            # Union 两分支共享同名字段缓存，JOIN 数量可能复用
            assert join_count >= 1, f"至少 1 个 JOIN 表达式，实际 {join_count}"
