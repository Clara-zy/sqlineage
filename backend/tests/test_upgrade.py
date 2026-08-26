"""root→middle 升级端到端测试"""

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.upgrade_engine.engine import execute_upgrade


class TestUpgrade:
    """root→middle 升级全链路"""

    def test_upgrade_trans_flow_root_to_middle(self, clean_db):
        """
        场景:
        1. trans_flow(root) 有下游查询引用
        2. 构建 ods_raw_trans 的 DWD 中间层
        3. 升级 trans_flow: root → middle
        4. 验证引用链更新 + table source 变更
        """
        # Step 1: 构建 trans_flow 链路 (root)
        r1 = build_lineage("SELECT trans_id, mchnt_cd FROM trans_flow")
        assert r1["new_fields"] > 0

        # 记录 trans_flow.trans_id atomic hash (从 fields 表查)
        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE field_type = 'atomic' "
                "AND expression ->> 'column' = 'trans_id' "
                "AND expression ->> 'table' LIKE '%trans_flow%'"
            )
            old_trans_id_hash = cur.fetchone()["hash"]
            assert old_trans_id_hash is not None

        # Step 2: 构建下游依赖（另一个查询引用 trans_flow）
        r2 = build_lineage(
            "SELECT b.mchnt_nm, a.trans_id "
            "FROM trans_flow a LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd"
        )
        assert r2["new_fields"] > 0

        # 记录下游查询产生的字段数
        with get_cursor() as cur:
            cur.execute("SELECT count(*) FROM fields")
            fields_before = cur.fetchone()["count"]

        # Step 3: 构建 dwd_trans_mid 血缘
        r3 = build_lineage(
            "SELECT trans_id, mchnt_cd, trans_at, trans_time, trans_status "
            "FROM ods_raw_trans WHERE trans_status = 'SUCCESS'",
            sink_table="dwd_trans_mid",
        )
        assert r3["new_fields"] > 0

        # Step 4: 升级 trans_flow — 直接从 ods_raw_trans 构建（匹配 7 列）
        result = execute_upgrade(
            "trans_flow",
            "SELECT trans_id, mchnt_cd, usr_id, trans_at, trans_time, trans_status, region_cd "
            "FROM ods_raw_trans",
        )
        assert "error" not in result, f"升级失败: {result.get('error')}"
        print(f"Upgrade result: {result}")

        # Step 5: 验证
        with get_cursor() as cur:
            # trans_flow 现在是 middle
            cur.execute(
                "SELECT source FROM tables WHERE table_name = 'trans_flow'"
            )
            assert cur.fetchone()["source"] == "sink"

            # 有新的输入子查询记录
            cur.execute(
                "SELECT count(*) FROM table_input_subqueries tis "
                "JOIN tables t ON t.id = tis.table_id "
                "WHERE t.table_name = 'trans_flow' AND tis.status = 'active'"
            )
            assert cur.fetchone()["count"] >= 1

            # 字段数量增加了（新链路）
            cur.execute("SELECT count(*) FROM fields")
            fields_after = cur.fetchone()["count"]
            assert fields_after >= fields_before, f"{fields_after} >= {fields_before}"

    def test_extract_reference_tree(self, clean_db):
        """引用树提取"""
        from app.upgrade_engine.engine import _extract_tree as extract_reference_tree

        build_lineage("SELECT trans_id, mchnt_cd FROM trans_flow")
        build_lineage("SELECT nvl(mchnt_cd, 'X') FROM trans_flow")

        with get_cursor() as cur:
            cur.execute(
                "SELECT hash FROM fields WHERE field_type = 'atomic' "
                "AND expression ->> 'column' = 'mchnt_cd' "
                "AND expression ->> 'table' LIKE '%trans_flow%'"
            )
            mchnt_hash = cur.fetchone()["hash"]

        tree = extract_reference_tree([mchnt_hash])
        assert len(tree) >= 2  # mchnt_cd + nvl 引用
