"""Layer 0 测试 — 单表无变换血缘构建"""

import pytest
from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.lineage_engine.validators import ValidationError


class TestLayer0:
    """单表无变换: SELECT a, b FROM t"""

    def test_simple_select_all_columns(self, clean_db):
        result = build_lineage("SELECT trans_id, mchnt_cd, trans_at FROM trans_flow")
        assert result["new_fields"] >= 3

    def test_select_subset(self, clean_db):
        result = build_lineage("SELECT trans_id, trans_at FROM trans_flow")
        assert result["new_fields"] >= 2

    def test_idempotent(self, clean_db):
        r1 = build_lineage("SELECT trans_id, trans_at FROM trans_flow")
        r2 = build_lineage("SELECT trans_id, trans_at FROM trans_flow")
        assert r2["new_fields"] == 0
        assert r2["new_subqueries"] == 0

    def test_reuse_across_queries(self, clean_db):
        build_lineage("SELECT trans_id FROM trans_flow")
        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE field_type = 'atomic' "
                "AND expression->>'column' = 'trans_id'"
            )
            hash1 = cur.fetchone()["hash"]

        build_lineage("SELECT trans_id, mchnt_cd FROM trans_flow")
        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE field_type = 'atomic' "
                "AND expression->>'column' = 'trans_id'"
            )
            hash2 = cur.fetchone()["hash"]

        assert hash1 == hash2

    def test_limit_rejected(self, clean_db):
        with pytest.raises(ValidationError) as exc:
            build_lineage("SELECT trans_id FROM trans_flow LIMIT 10")
        assert exc.value.code == 49001

    def test_unknown_table_rejected(self, clean_db):
        with pytest.raises(ValidationError) as exc:
            build_lineage("SELECT x FROM unknown_table")
        assert exc.value.code == 49003
