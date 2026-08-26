"""SQL 还原引擎测试 — 多表 JOIN 还原顺序与 ON 关联"""

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.sql_reconstructor import field_to_sql_full


def _build_and_get_struct(sql):
    build_lineage(sql)
    with get_cursor() as cur:
        cur.execute("SELECT subquery_hash FROM build_history ORDER BY id DESC LIMIT 1")
        sq_hash = cur.fetchone()["subquery_hash"]
        cur.execute("SELECT structure FROM subqueries WHERE hash = %s", (sq_hash,))
        struct = cur.fetchone()["structure"]
    return sq_hash, struct


class TestJoinReconstruct:
    def test_l9_01_three_inner_join_order(self, clean_db):
        """3 表连续 INNER JOIN：基表正确、JOIN 顺序与 ON 一一对应"""
        sql = (
            "SELECT a.trans_id, b.mchnt_nm, c.industry_nm "
            "FROM trans_flow a "
            "INNER JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd "
            "INNER JOIN dim_industry c ON b.industry_id = c.industry_id"
        )
        sq_hash, struct = _build_and_get_struct(sql)
        alias_map = struct["alias_map"]

        # a.trans_id：列属于 trans_flow，基表必须是 trans_flow（而非字母序的 dim_industry）
        trans_sql = field_to_sql_full(alias_map["trans_id"], sq_hash)
        lines = trans_sql.splitlines()
        assert lines[1] == "from trans_flow"
        assert lines[2].startswith("  join dim_mchnt on"), lines[2]
        assert "mchnt_cd" in lines[2]
        assert lines[3].startswith("  join dim_industry on"), lines[3]
        assert "industry_id" in lines[3]

        # b.mchnt_nm：列属于 dim_mchnt，基表为 dim_mchnt，仍先连 trans_flow 再连 dim_industry
        mchnt_sql = field_to_sql_full(alias_map["mchnt_nm"], sq_hash)
        mlines = mchnt_sql.splitlines()
        assert mlines[1] == "from dim_mchnt"
        assert mlines[2].startswith("  join trans_flow on"), mlines[2]
        assert mlines[3].startswith("  join dim_industry on"), mlines[3]

        # c.industry_nm：仅一层 JOIN（cond2），基表为 dim_industry
        industry_sql = field_to_sql_full(alias_map["industry_nm"], sq_hash)
        ilines = industry_sql.splitlines()
        assert ilines[1] == "from dim_industry"
        assert ilines[2].startswith("  join dim_mchnt on"), ilines[2]
        assert len(ilines) == 3, f"c 列应只有 1 个 JOIN，实际 {len(ilines)-2} 个"


class TestClassification:
    """节点分类规约 _SPEC 的还原覆盖"""

    def test_unknown_scalar_uses_prefix_fallback(self, clean_db):
        """未知标量函数 op → prefix 兜底，而非 <op> 占位符"""
        sql = "SELECT date_format(trans_time, 'yyyyMMdd') AS dt FROM trans_flow"
        sq_hash, struct = _build_and_get_struct(sql)
        dt_sql = field_to_sql_full(struct["alias_map"]["dt"], sq_hash)
        assert "DATE_FORMAT(trans_flow.trans_time, 'yyyyMMdd')" in dt_sql
        assert "<" not in dt_sql

    def test_casewhen_collects_tables(self, clean_db):
        """casewhen 的 {condition, value} 分支应被正确收集表，FROM 不为 ?"""
        sql = (
            "SELECT CASE WHEN trans_at > 10000 THEN 'big' ELSE 'small' END AS lvl "
            "FROM trans_flow"
        )
        sq_hash, struct = _build_and_get_struct(sql)
        lvl_sql = field_to_sql_full(struct["alias_map"]["lvl"], sq_hash)
        assert "CASE WHEN" in lvl_sql
        assert "from trans_flow" in lvl_sql

    def test_window_renders(self, clean_db):
        """window 节点渲染 ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)"""
        sql = (
            "SELECT mchnt_cd, "
            "row_number() OVER (PARTITION BY mchnt_cd ORDER BY trans_time) AS rn "
            "FROM trans_flow"
        )
        sq_hash, struct = _build_and_get_struct(sql)
        rn_sql = field_to_sql_full(struct["alias_map"]["rn"], sq_hash)
        assert "ROW_NUMBER() OVER" in rn_sql
        assert "PARTITION BY trans_flow.mchnt_cd" in rn_sql
        assert "ORDER BY trans_flow.trans_time" in rn_sql
        assert "from trans_flow" in rn_sql

    def test_alias_reflected(self, clean_db):
        """alias 装饰节点应体现 AS 别名，而非透明穿透"""
        sql = "SELECT trans_status AS status FROM trans_flow"
        sq_hash, struct = _build_and_get_struct(sql)
        status_sql = field_to_sql_full(struct["alias_map"]["status"], sq_hash)
        assert "trans_flow.trans_status AS status" in status_sql

    def test_is_not_null_infix(self, clean_db):
        """IS NULL + NOT 应渲染为 infix/unary，而非函数调用 IS(...)"""
        sql = "SELECT count(*) AS cnt FROM trans_flow WHERE trans_at IS NOT NULL"
        sq_hash, struct = _build_and_get_struct(sql)
        cnt_sql = field_to_sql_full(struct["alias_map"]["cnt"], sq_hash)
        assert "trans_flow.trans_at IS NULL" in cnt_sql
        assert "NOT trans_flow.trans_at" in cnt_sql
        assert "IS(" not in cnt_sql

    def test_like_infix(self, clean_db):
        """LIKE 应渲染为 infix，而非函数调用 LIKE(...)"""
        sql = "SELECT trans_id FROM trans_flow WHERE trans_status LIKE 'SU%'"
        sq_hash, struct = _build_and_get_struct(sql)
        trans_sql = field_to_sql_full(struct["alias_map"]["trans_id"], sq_hash)
        assert "trans_flow.trans_status LIKE 'SU%'" in trans_sql

    def test_paren_preserved(self, clean_db):
        """括号应保留（避免优先级错误），而非渲染成 PAREN(...)"""
        sql = "SELECT (trans_at + 5) * 2 AS x FROM trans_flow"
        sq_hash, struct = _build_and_get_struct(sql)
        x_sql = field_to_sql_full(struct["alias_map"]["x"], sq_hash)
        assert "PAREN(" not in x_sql
        assert "* 2" in x_sql

    def test_expression_propagates_where(self, clean_db):
        """表达式节点（round 等）应向上传播子节点携带的 WHERE，而非丢弃"""
        sql = (
            "SELECT round(trans_at, 2) AS y FROM trans_flow "
            "WHERE trans_status = 'SUCCESS'"
        )
        sq_hash, struct = _build_and_get_struct(sql)
        y_sql = field_to_sql_full(struct["alias_map"]["y"], sq_hash)
        assert "ROUND(trans_flow.trans_at, 2) AS y" in y_sql
        assert "where trans_flow.trans_status = 'SUCCESS'" in y_sql

    def test_where_condition_merges_join(self, clean_db):
        """WHERE 条件引用的 join 表应被合并进 FROM（LEFT JOIN 投影左表列，WHERE 引用右表列）"""
        sql = (
            "SELECT a.trans_id FROM trans_flow a "
            "LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd "
            "WHERE b.mchnt_nm = 'X'"
        )
        sq_hash, struct = _build_and_get_struct(sql)
        trans_sql = field_to_sql_full(struct["alias_map"]["trans_id"], sq_hash)
        assert "join" in trans_sql
        assert "where dim_mchnt.mchnt_nm = 'X'" in trans_sql
