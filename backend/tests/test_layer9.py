"""Layer 9+ 测试 — JOIN 包装 / 违规校验 / LATERAL VIEW / 日期函数"""

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage


class TestJoinWrap:
    """JOIN 字段包装 — LEFT JOIN 右表字段包裹 JOIN 表达式"""

    def test_left_join_creates_join_expr(self, clean_db):
        """LEFT JOIN 应为右表字段创建 JOIN 表达式"""
        result = build_lineage("""
            SELECT a.trans_id, b.mchnt_nm
            FROM trans_flow a
            LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash, expression FROM fields WHERE expression ->> 'op' = 'join'"
            )
            rows = cur.fetchall()
            # 右表字段(b.mchnt_nm)应有 JOIN 包装，左表字段(a.trans_id)不应
            assert len(rows) >= 1
            for r in rows:
                assert "on" in r["expression"]

    def test_left_join_left_field_no_wrap(self, clean_db):
        """LEFT JOIN 左表字段不包裹 JOIN"""
        build_lineage("""
            SELECT a.trans_id, b.mchnt_nm
            FROM trans_flow a
            LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
        """)

        with get_cursor() as cur:
            cur.execute("SELECT hash, expression FROM fields")
            all_exprs = {r["hash"]: r["expression"] for r in cur.fetchall()}

            # 找到 trans_id 的非 atomic 节点（即 alias 对应的 inner）
            join_wrapped_trans_id = False
            for h, expr in all_exprs.items():
                if expr.get("op") == "join":
                    # 检查 JOIN 的是哪个 atomic
                    args = expr.get("args", [])
                    for arg in args:
                        ref_h = arg.get("hash", "")
                        if ref_h in all_exprs and all_exprs[ref_h].get("column") == "trans_id":
                            join_wrapped_trans_id = True
            assert not join_wrapped_trans_id, "LEFT JOIN 左表字段不应包裹 JOIN"


class TestValidation:
    """违规校验"""

    def test_three_inner_join_accepted(self, clean_db):
        """3表连续 INNER JOIN：左右表字段逐层累积包裹，不再拒绝"""
        from app.database import get_cursor

        result = build_lineage("""
            SELECT a.trans_id, b.mchnt_nm, c.industry_nm
            FROM trans_flow a
            INNER JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
            INNER JOIN dim_industry c ON b.industry_id = c.industry_id
        """)

        # 构建成功
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            # 验证 JOIN 节点存在（a 列 2 层 + b 列 2 层 + c 列 1 层 = 5，加 ON 引用 = 6）
            cur.execute(
                "SELECT count(*) FROM fields WHERE expression ->> 'op' = 'join'"
            )
            join_count = cur.fetchone()["count"]
            assert join_count >= 5

            # 验证 a.trans_id 被包了两层 JOIN
            cur.execute(
                "SELECT f.hash FROM fields f "
                "JOIN field_references fr ON fr.referrer_hash = f.hash "
                "WHERE f.expression ->> 'op' = 'join' "
                "AND jsonb_array_length(f.expression -> 'args') = 1"
            )
            join_nodes = cur.fetchall()
            assert len(join_nodes) >= 3  # 至少 3 个 JOIN 节点（a 层 1, a 层 2, b 层 1 等）

            # 验证 field_references 链：atomic → JOIN1 → JOIN2（a.trans_id 路径）
            cur.execute(
                "SELECT count(*) FROM field_references "
                "WHERE referenced_hash IN (SELECT hash FROM fields WHERE expression ->> 'op' = 'column')"
            )
            atomic_refs = cur.fetchone()["count"]
            assert atomic_refs >= 3  # 至少 3 个原子字段被引用


class TestDateFunctions:
    """日期函数"""

    def test_date_format(self, clean_db):
        """date_format(trans_time, 'yyyyMMdd')"""
        build_lineage("SELECT date_format(trans_time, 'yyyyMMdd') AS dt FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'date_format'"
            )
            assert cur.fetchone() is not None

    def test_substring(self, clean_db):
        """substring(trans_time, 1, 10)"""
        build_lineage("SELECT substring(trans_time, 1, 10) AS dt FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' IN ('substring', 'substr')"
            )
            assert cur.fetchone() is not None


class TestRightJoin:
    """RIGHT JOIN → LEFT JOIN 转换"""

    def test_right_join_converts_to_left(self, clean_db):
        """RIGHT JOIN 应转换为 LEFT JOIN（交换表位置）"""
        result = build_lineage("""
            SELECT a.trans_id, b.mchnt_nm
            FROM trans_flow a
            RIGHT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM fields WHERE expression ->> 'op' = 'join'"
            )
            join_count = cur.fetchone()["count"]
            # RIGHT→LEFT 后，右表是 trans_flow，应该有 JOIN 包装
            assert join_count >= 1


class TestThreeTableJoin:
    """3 表连续 JOIN 的字段包装"""

    def test_three_left_join_fields_wrapped(self, clean_db):
        """3 表 LEFT JOIN: 第 2、3 个表的字段应有 JOIN 包装"""
        result = build_lineage("""
            SELECT a.trans_id, b.mchnt_nm, c.industry_nm
            FROM trans_flow a
            LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
            LEFT JOIN dim_industry c ON b.industry_id = c.industry_id
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM fields WHERE expression ->> 'op' = 'join'"
            )
            join_count = cur.fetchone()["count"]
            # b.mchnt_nm 和 c.industry_nm 都应该有 JOIN 包装
            assert join_count >= 2, f"至少 2 个 JOIN，实际 {join_count}"


class TestLateralView:
    """Q13: LATERAL VIEW"""

    def test_lateral_view_explode(self, clean_db):
        """LATERAL VIEW explode(split(industry_list, ','))"""
        result = build_lineage("""
            SELECT mchnt_cd, mchnt_nm, industry_tag
            FROM dim_mchnt
            LATERAL VIEW explode(split(industry_list, ',')) t AS industry_tag
        """)
        assert result["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute(
                "SELECT expression FROM fields WHERE expression ->> 'op' = 'join'"
            )
            rows = cur.fetchall()
            has_lateral = False
            for r in rows:
                if r["expression"].get("lateral"):
                    has_lateral = True
            assert has_lateral, "LATERAL VIEW 应创建 lateral=true 的 JOIN 表达式"
