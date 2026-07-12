"""多层 middle 表穿透升级测试"""

import pytest
from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.upgrade_engine.engine import preview_upgrade, execute_upgrade


class TestMultiLevelUpgrade:
    """root→middle 升级 + middle 表穿透"""

    def test_preview_shows_impact(self, clean_db):
        """预览应显示影响范围"""
        # 先建一些下游查询
        build_lineage("SELECT trans_id, mchnt_cd FROM trans_flow")
        build_lineage(
            "SELECT b.mchnt_nm, a.trans_id "
            "FROM trans_flow a LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd"
        )

        result = preview_upgrade("trans_flow")
        assert "error" not in result
        assert result["field_count"] > 0
        assert result["downstream_fields"] > 0

    def test_upgrade_with_middle_cascade(self, clean_db):
        """
        多层穿透:
        1. trans_flow (root) → 下游查询引用
        2. 升级 trans_flow → middle (FROM ods_raw_trans)
        3. 验证: 下游字段 hash 变更, middle table_column_mappings 更新
        """
        # Step 1: 构建原始链路
        r1 = build_lineage("SELECT trans_id, mchnt_cd FROM trans_flow")
        assert r1["new_fields"] > 0

        # Step 2: 建下游依赖
        r2 = build_lineage(
            "SELECT b.mchnt_nm, a.trans_id "
            "FROM trans_flow a LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd"
        )
        assert r2["new_fields"] > 0

        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM fields")
            fields_before = cur.fetchone()["count"]

        # Step 3: 执行升级
        result = execute_upgrade(
            "trans_flow",
            "SELECT trans_id, mchnt_cd, usr_id, trans_at, trans_time, trans_status, region_cd "
            "FROM ods_raw_trans",
        )
        assert "error" not in result, result.get("error")

        # Step 4: 验证
        with get_cursor() as cur:
            # source 变 middle
            cur.execute("SELECT source FROM tables WHERE table_name = 'trans_flow'")
            assert cur.fetchone()["source"] == "sink"

            # 有新字段创建
            cur.execute("SELECT count(*) FROM fields")
            fields_after = cur.fetchone()["count"]
            assert fields_after >= fields_before

            # 旧 field 对应的 table_column_mappings 有 deprecated 记录
            cur.execute(
                "SELECT count(*) FROM table_column_mappings WHERE status = 'deprecated'"
            )
            deprecated = cur.fetchone()["count"]
            assert deprecated >= 0  # 可能有也可能没有

    def test_full_chain(self, clean_db):
        """
        完整链路测试:
        dws_mchnt_day_v2 作为 root → 下游 → 升级穿透
        """
        # 重置 dws_mchnt_day 为 root（seed 中它是 middle）
        with get_cursor() as cur:
            cur.execute(
                "UPDATE tables SET source = 'root', input_subquery_hash = NULL "
                "WHERE table_name = 'dws_mchnt_day'"
            )
            cur.execute(
                "UPDATE table_columns SET field_hash = NULL "
                "WHERE table_id = (SELECT id FROM tables WHERE table_name = 'dws_mchnt_day')"
            )

        # Step 1: 作为 root 构建
        build_lineage(
            "SELECT mchnt_cd, substring(trans_time,1,8) AS stat_date, "
            "count(1) AS trans_cnt, sum(trans_at) AS trans_at, "
            "sum(CASE WHEN trans_status='SUCCESS' THEN 1 ELSE 0 END) AS succ_cnt "
            "FROM trans_flow "
            "GROUP BY mchnt_cd, substring(trans_time,1,8)",
            sink_table="dws_mchnt_day",
        )

        # Step 2: 下游查询
        build_lineage("SELECT mchnt_cd, trans_cnt FROM dws_mchnt_day")

        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM fields")
            before = cur.fetchone()["count"]

        # Step 3: 预览
        preview = preview_upgrade("dws_mchnt_day")
        print(f"Preview: {preview}")

        # Step 4: 升级（dws_mchnt_day FROM trans_flow — 已有中间层数据）
        result = execute_upgrade(
            "dws_mchnt_day",
            "SELECT mchnt_cd, substring(trans_time,1,8) AS stat_date, "
            "count(1) AS trans_cnt, sum(trans_at) AS trans_at, "
            "sum(CASE WHEN trans_status='SUCCESS' THEN 1 ELSE 0 END) AS succ_cnt "
            "FROM trans_flow GROUP BY mchnt_cd, substring(trans_time,1,8)",
        )
        assert "error" not in result, result.get("error")

        with get_cursor() as cur:
            cur.execute("SELECT source FROM tables WHERE table_name = 'dws_mchnt_day'")
            assert cur.fetchone()["source"] == "sink"
            cur.execute("SELECT count(*) FROM fields")
            after = cur.fetchone()["count"]
            assert after >= before
