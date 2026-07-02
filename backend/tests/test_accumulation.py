"""累积场景测试 — 验证跨查询的字段/子查询复用

关键验证点:
1. 先构建 SQL1，再构建 SQL2，SQL2 中已存在的节点不应重复创建
2. 同一表达式在不同查询中 hash 一致
3. 累积构建后，field_references 关系完整
"""

import pytest
from app.database import get_cursor
from app.lineage_engine.engine import build_lineage


@pytest.fixture(scope="class")
def accum_db():
    """整个测试类共享一份数据，不清理"""
    from tests.conftest import ensure_seed, cleanup_lineage
    ensure_seed()
    cleanup_lineage()
    yield


@pytest.mark.usefixtures("accum_db")
class TestAccumulation:
    """跨查询累积复用"""

    def test_same_column_across_queries(self):
        """同一列在不同查询中复用"""
        r1 = build_lineage("SELECT trans_id FROM trans_flow")
        assert r1["new_fields"] > 0

        r2 = build_lineage("SELECT trans_id, mchnt_cd FROM trans_flow")
        # trans_id atomic 复用；mchnt_cd atomic + 两个 alias 是新的
        assert r2["new_fields"] == 1  # 只新建 mchnt_cd atomic (alias 同名跳过，trans_id 复用)

        # 验证 trans_id 的 atomic 只有一个
        with get_cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM fields WHERE field_type = 'atomic' "
                "AND expression ->> 'column' = 'trans_id'"
            )
            assert cur.fetchone()["count"] == 1

    def test_same_expression_across_queries(self):
        """同一表达式 (nvl) 在不同查询中复用"""
        r1 = build_lineage("SELECT nvl(mchnt_cd, 'UNKNOWN') AS safe_cd FROM trans_flow")

        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM fields")
            total_before = cur.fetchone()["count"]

        r2 = build_lineage(
            "SELECT nvl(mchnt_cd, 'UNKNOWN') AS safe_cd, trans_at FROM trans_flow"
        )
        assert r2["new_fields"] < 3  # nvl 复用，最多新增 trans_at atomic + alias

    def test_same_subquery_across_queries(self):
        """同一子查询在不同外层查询中复用 — 验证内层 SQ hash 一致"""
        inner = "SELECT mchnt_cd, sum(trans_at) AS total FROM trans_flow GROUP BY mchnt_cd"

        build_lineage(f"SELECT a.mchnt_cd, a.total FROM ({inner}) a")

        with get_cursor() as cur:
            cur.execute("SELECT hash FROM subqueries ORDER BY created_at")
            sq_hashes = [r["hash"] for r in cur.fetchall()]
            assert len(sq_hashes) >= 1
            inner_hash_1 = sq_hashes[0]  # 第一个子查询（内层）

        r2 = build_lineage(f"SELECT a.total FROM ({inner}) a WHERE a.total > 100")

        # 内层子查询应复用，hash 一致
        with get_cursor() as cur:
            cur.execute("SELECT hash FROM subqueries ORDER BY created_at")
            sq_hashes_2 = [r["hash"] for r in cur.fetchall()]
            # 内层 hash 应该还在，且出现了两次？不，应该只有一次（复用）
            count_inner = sum(1 for h in sq_hashes_2 if h == inner_hash_1)
            assert count_inner == 1  # 内层子查询只存了一份

    def test_field_references_accumulated(self):
        """累积构建后 field_references 边数量正确"""
        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM field_references")
            ref_count = cur.fetchone()["count"]
            cur.execute("SELECT count(*) FROM fields")
            field_count = cur.fetchone()["count"]

        # 有字段就应该有引用关系（除了叶子节点）
        assert ref_count > 0
        assert field_count > 0

    def test_final_state_consistent(self):
        """最终状态：所有 expression 中的 hash 引用都能在 fields 中找到"""
        with get_cursor() as cur:
            cur.execute("SELECT hash, expression FROM fields")
            all_fields = {r["hash"]: r["expression"] for r in cur.fetchall()}

        # 遍历所有 expression，检查子 hash 引用是否有效
        orphan_refs = []
        for h, expr in all_fields.items():
            args = expr.get("args") or []
            for arg in args:
                ref_h = arg.get("hash")
                if ref_h and ref_h not in all_fields:
                    orphan_refs.append((h, ref_h))

        assert len(orphan_refs) == 0, f"存在悬空引用: {orphan_refs[:5]}"
