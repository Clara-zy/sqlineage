"""Layer 1 测试 — 单表表达式: nvl / round / concat / CAST / 算术运算"""

import pytest
from app.database import get_cursor
from app.lineage_engine.engine import build_lineage


class TestLayer1:
    """单表复杂表达式"""

    def test_nvl_expression(self, clean_db):
        """nvl(mchnt_cd, 'UNKNOWN')"""
        build_lineage("SELECT nvl(mchnt_cd, 'UNKNOWN') AS safe_cd FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash, field_type, expression FROM fields WHERE field_type = 'expression'"
            )
            rows = [dict(r) for r in cur.fetchall()]

            # 应该有 nvl 表达式节点
            nvl_fields = [r for r in rows if r["expression"].get("op") == "nvl"]
            assert len(nvl_fields) >= 1

            # nvl 的子节点应该包含一个 atomic 字段和一个 literal
            nvl = nvl_fields[0]
            child_hashes = [a["hash"] for a in nvl["expression"]["args"]]
            assert len(child_hashes) >= 2

    def test_round_with_division(self, clean_db):
        """round(trans_at / 100, 2)"""
        build_lineage("SELECT round(trans_at / 100, 2) AS yuan FROM trans_flow")

        with get_cursor() as cur:
            cur.execute("SELECT hash, expression FROM fields")
            rows = {r["hash"]: r["expression"] for r in cur.fetchall()}

            # 找 round 节点
            round_exprs = [
                e for e in rows.values() if e.get("op") == "round"
            ]
            assert len(round_exprs) >= 1

    def test_concat_expression(self, clean_db):
        """concat(trans_status, '_', 'SUFFIX')"""
        build_lineage(
            "SELECT concat(trans_status, '_', 'SUFFIX') AS tagged FROM trans_flow"
        )

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'concat'"
            )
            rows = cur.fetchall()
            assert len(rows) >= 1

    def test_cast_expression(self, clean_db):
        """CAST(region_cd AS STRING)"""
        build_lineage("SELECT CAST(region_cd AS STRING) AS rc FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'cast'"
            )
            rows = cur.fetchall()
            assert len(rows) >= 1

    def test_combined_expressions(self, clean_db):
        """Q5 完整查询"""
        result = build_lineage("""
            SELECT
                trans_id,
                nvl(mchnt_cd, 'UNKNOWN') AS mchnt_cd,
                round(trans_at / 100, 2) AS trans_at_yuan,
                concat(trans_status, '_', CAST(region_cd AS STRING)) AS status_region
            FROM trans_flow
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM fields WHERE field_type = 'expression'")
            expr_count = cur.fetchone()["count"]
            assert expr_count >= 4  # nvl + div + round + concat + cast 各自

    def test_arithmetic_binary(self, clean_db):
        """trans_at * 1.1 + 5"""
        build_lineage("SELECT trans_at * 1.1 + 5 AS adjusted FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash, expression FROM fields WHERE expression ->> 'op' IN ('add', 'mul')"
            )
            rows = cur.fetchall()
            assert len(rows) >= 2  # mul + add

    def test_nvl_reuse(self, clean_db):
        """nvl(mchnt_cd, 'UNKNOWN') 在不同查询中复用"""
        build_lineage("SELECT nvl(mchnt_cd, 'UNKNOWN') AS a FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'nvl'"
            )
            hash1 = cur.fetchone()["hash"]

        build_lineage("SELECT nvl(mchnt_cd, 'UNKNOWN') AS b FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'nvl'"
            )
            hash2 = cur.fetchone()["hash"]

        assert hash1 == hash2
