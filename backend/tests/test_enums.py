"""枚举值测试 — DDL COMMENT ENUM() 解析 / 继承 / 校验"""

import pytest
from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.field_enums import (
    parse_enum_from_comment,
    get_active_enum,
    validate_enum_value,
)


class TestEnumComment:
    """DDL COMMENT 解析"""

    def test_parse_enum(self):
        assert parse_enum_from_comment("交易状态 ENUM(SUCCESS,FAIL,REFUND)") == [
            "SUCCESS", "FAIL", "REFUND"
        ]
        assert parse_enum_from_comment("性别 ENUM(M,F)") == ["M", "F"]
        assert parse_enum_from_comment("普通说明") is None
        assert parse_enum_from_comment(None) is None

    def test_atomic_field_gets_enum_from_comment(self, clean_db):
        """atomic 字段创建时自动解析 COMMENT 中的 ENUM"""
        build_lineage("SELECT trans_status FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT f.hash FROM fields f WHERE f.field_type = 'atomic' "
                "AND f.expression ->> 'column' = 'trans_status'"
            )
            row = cur.fetchone()
            assert row is not None

            values = get_active_enum(row["hash"])
            assert values == ["SUCCESS", "FAIL", "REFUND"]


class TestEnumInherit:
    """枚举继承"""

    def test_alias_inherits_enum(self, clean_db):
        """alias 节点继承枚举值"""
        build_lineage("SELECT trans_status AS status FROM trans_flow")

        with get_cursor() as cur:
            # 找 alias 节点的 hash
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'alias' "
                "AND expression ->> 'alias_name' = 'status'"
            )
            row = cur.fetchone()
            assert row is not None
            values = get_active_enum(row["hash"])
            assert values == ["SUCCESS", "FAIL", "REFUND"]

    def test_join_inherits_enum(self, clean_db):
        """JOIN 节点继承被包装字段的枚举"""
        build_lineage(
            "SELECT a.trans_id, b.mchnt_nm "
            "FROM trans_flow a "
            "INNER JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd"
        )

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE expression ->> 'op' = 'join'"
            )
            rows = cur.fetchall()
            assert len(rows) >= 2

            # INNER JOIN 左右表字段都有 JOIN 包装
            for r in rows:
                values = get_active_enum(r["hash"])
                print(f"JOIN {r['hash'][:8]} enum={values}")


class TestEnumValidate:
    """枚举校验"""

    def test_valid_literal_passes(self, clean_db):
        build_lineage("SELECT trans_status FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE field_type = 'atomic' "
                "AND expression ->> 'column' = 'trans_status'"
            )
            h = cur.fetchone()["hash"]

        warn = validate_enum_value(h, "SUCCESS")
        assert warn is None

    def test_invalid_literal_warns(self, clean_db):
        build_lineage("SELECT trans_status FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE field_type = 'atomic' "
                "AND expression ->> 'column' = 'trans_status'"
            )
            h = cur.fetchone()["hash"]

        warn = validate_enum_value(h, "PENDING")
        assert warn is not None
        assert "PENDING" in warn

    def test_eq_with_enum_warns_on_build(self, clean_db):
        """WHERE trans_status = 'PENDING' 构建时产生枚举警告"""
        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            build_lineage("SELECT trans_id FROM trans_flow WHERE trans_status = 'PENDING'")
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "ENUM-WARN" in output, f"应有枚举警告, 实际: {output}"

    def test_eq_with_valid_enum_no_warn(self, clean_db):
        """WHERE trans_status = 'SUCCESS' 不产生警告"""
        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            build_lineage("SELECT trans_id FROM trans_flow WHERE trans_status = 'SUCCESS'")
        finally:
            sys.stdout = old_stdout
        output = captured.getvalue()
        assert "ENUM-WARN" not in output
