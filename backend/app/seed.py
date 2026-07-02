"""种子数据 — 灌入 7 张测试表的元数据

运行: make db-init (已包含) 或单独: python backend/app/seed.py
"""

from app.database import get_cursor

ROOT_TABLES = {
    "trans_flow": {
        "table_type": "事实表",
        "columns": [
            ("trans_id", "STRING", "交易流水号"),
            ("mchnt_cd", "STRING", "商户号"),
            ("usr_id", "STRING", "用户ID"),
            ("trans_at", "DECIMAL(18,2)", "交易金额(元)"),
            ("trans_time", "STRING", "交易时间"),
            ("trans_status", "STRING", "SUCCESS/FAIL/REFUND"),
            ("region_cd", "STRING", "交易发生地区划代码"),
        ],
    },
    "dim_mchnt": {
        "table_type": "维度表",
        "columns": [
            ("mchnt_cd", "STRING", "商户号"),
            ("mchnt_nm", "STRING", "商户名称"),
            ("industry_id", "STRING", "行业ID"),
            ("industry_list", "STRING", "行业列表(逗号分隔)"),
            ("region_cd", "STRING", "注册地区划代码"),
            ("register_time", "STRING", "注册时间"),
        ],
    },
    "dim_usr": {
        "table_type": "维度表",
        "columns": [
            ("usr_id", "STRING", "用户ID"),
            ("usr_nm", "STRING", "用户昵称"),
            ("gender", "STRING", "M/F"),
            ("usr_type", "STRING", "NORMAL/VIP"),
            ("register_time", "STRING", "注册时间"),
        ],
    },
    "dim_industry": {
        "table_type": "维度表",
        "columns": [
            ("industry_id", "STRING", "行业ID"),
            ("industry_nm", "STRING", "行业名称"),
            ("category", "STRING", "一级分类"),
        ],
    },
    "dim_domin": {
        "table_type": "维度表",
        "columns": [
            ("region_cd", "STRING", "区划代码"),
            ("region_nm", "STRING", "区划名称"),
            ("parent_region_cd", "STRING", "上级区划代码"),
        ],
    },
    "usr_behave_bury": {
        "table_type": "事实表",
        "columns": [
            ("event_id", "STRING", "事件ID"),
            ("usr_id", "STRING", "用户ID"),
            ("mchnt_cd", "STRING", "浏览的商户号"),
            ("behave_type", "STRING", "CLICK/VIEW/SEARCH/FAV/SHARE"),
            ("behave_time", "STRING", "行为时间"),
        ],
    },
}

MIDDLE_TABLES = {
    "dws_mchnt_day": {
        "table_type": "汇总表",
        "columns": [
            ("mchnt_cd", "STRING", "商户号"),
            ("stat_date", "STRING", "统计日期 yyyyMMdd"),
            ("trans_cnt", "BIGINT", "总交易笔数"),
            ("trans_at", "DECIMAL(18,2)", "总交易金额"),
            ("succ_cnt", "BIGINT", "成功交易笔数"),
        ],
    },
}


def seed():
    """灌入种子数据（幂等）"""
    with get_cursor() as cur:
        for name, info in ROOT_TABLES.items():
            # upsert tables
            cur.execute(
                "INSERT INTO tables (schema_name, table_name, table_type, source) "
                "VALUES ('', %s, %s, 'root') "
                "ON CONFLICT (schema_name, table_name) DO UPDATE SET source = 'root' "
                "RETURNING id",
                (name, info["table_type"]),
            )
            table_id = cur.fetchone()["id"]

            # upsert columns
            for i, (col_name, col_type, col_comment) in enumerate(info["columns"], 1):
                cur.execute(
                    "INSERT INTO table_columns "
                    "(table_id, column_name, column_type, column_comment, ordinal_position) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (table_id, column_name) DO UPDATE "
                    "SET column_type = %s, column_comment = %s, ordinal_position = %s",
                    (table_id, col_name, col_type, col_comment, i,
                     col_type, col_comment, i),
                )

        # middle 表
        for name, info in MIDDLE_TABLES.items():
            cur.execute(
                "INSERT INTO tables (schema_name, table_name, table_type, source) "
                "VALUES ('', %s, %s, 'middle') "
                "ON CONFLICT (schema_name, table_name) DO UPDATE SET source = 'middle' "
                "RETURNING id",
                (name, info["table_type"]),
            )
            table_id = cur.fetchone()["id"]

            for i, (col_name, col_type, col_comment) in enumerate(info["columns"], 1):
                cur.execute(
                    "INSERT INTO table_columns "
                    "(table_id, column_name, column_type, column_comment, ordinal_position) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (table_id, column_name) DO UPDATE "
                    "SET column_type = %s, column_comment = %s, ordinal_position = %s",
                    (table_id, col_name, col_type, col_comment, i,
                     col_type, col_comment, i),
                )

    print(f"✓ 种子数据就绪 — {len(ROOT_TABLES)} root 表 + {len(MIDDLE_TABLES)} middle 表")


if __name__ == "__main__":
    seed()
