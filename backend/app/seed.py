"""种子数据 — 灌入数据库 + 测试表的元数据

运行: make db-init (已包含)
"""

from app.database import get_cursor

DB_NAME = "mydb"

ROOT_TABLES = {
    "trans_flow": {
        "table_type": "事实表",
        "columns": [
            ("trans_id", "STRING", "交易流水号"),
            ("mchnt_cd", "STRING", "商户号"),
            ("usr_id", "STRING", "用户ID"),
            ("trans_at", "DECIMAL(18,2)", "交易金额(元)"),
            ("trans_time", "STRING", "交易时间"),
            ("trans_status", "STRING", "交易状态 ENUM(SUCCESS,FAIL,REFUND)"),
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
            ("gender", "STRING", "性别 ENUM(M,F)"),
            ("usr_type", "STRING", "用户类型 ENUM(NORMAL,VIP)"),
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
            ("behave_type", "STRING", "行为类型 ENUM(CLICK,VIEW,SEARCH,FAV,SHARE)"),
            ("behave_time", "STRING", "行为时间"),
        ],
    },
}

UPGRADE_TEST_TABLES = {
    "ods_raw_trans": {
        "table_type": "事实表",
        "columns": [
            ("trans_id", "STRING", "交易流水号"),
            ("mchnt_cd", "STRING", "商户号"),
            ("usr_id", "STRING", "用户ID"),
            ("trans_at", "DECIMAL(18,2)", "交易金额(元)"),
            ("trans_time", "STRING", "交易时间"),
            ("trans_status", "STRING", "交易状态 ENUM(SUCCESS,FAIL,REFUND)"),
            ("region_cd", "STRING", "区划代码"),
        ],
    },
    "dwd_trans_mid": {
        "table_type": "中间表",
        "columns": [
            ("trans_id", "STRING", "交易流水号"),
            ("mchnt_cd", "STRING", "商户号"),
            ("trans_at", "DECIMAL(18,2)", "交易金额"),
            ("trans_time", "STRING", "交易时间"),
            ("trans_status", "STRING", "交易状态 ENUM(SUCCESS,FAIL,REFUND)"),
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


BIRD_TYPE_MAP = {
    "text": "STRING",
    "integer": "INTEGER",
    "real": "DECIMAL",
    "date": "DATE",
}


def seed():
    """灌入种子数据（幂等）"""
    _seed_default()
    _seed_bird()


def _seed_default():
    """原有默认数据：mydb + 9 张表"""
    with get_cursor() as cur:
        # 数据库
        cur.execute(
            "INSERT INTO databases (name, description) VALUES (%s, '默认数据库') "
            "ON CONFLICT (name) DO NOTHING RETURNING id",
            (DB_NAME,),
        )
        row = cur.fetchone()
        db_id = row["id"] if row else _get_db_id(cur, DB_NAME)

        for name, info in ROOT_TABLES.items():
            _upsert_table(cur, db_id, name, info, "root")
        for name, info in MIDDLE_TABLES.items():
            _upsert_table(cur, db_id, name, info, "sink")
        for name, info in UPGRADE_TEST_TABLES.items():
            _upsert_table(cur, db_id, name, info, "root")

    print(f"✓ 默认数据就绪 — {DB_NAME}: {len(ROOT_TABLES)} root + {len(MIDDLE_TABLES)} middle + {len(UPGRADE_TEST_TABLES)} 升级测试表")


def _get_db_id(cur, name: str) -> int:
    cur.execute("SELECT id FROM databases WHERE name = %s", (name,))
    return cur.fetchone()["id"]


def _upsert_table(cur, db_id: int, name: str, info: dict, source: str):
    cur.execute(
        "INSERT INTO tables (database_id, table_name, table_type, source) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (database_id, table_name) DO UPDATE SET source = %s "
        "RETURNING id",
        (db_id, name, info["table_type"], source, source),
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


def _seed_bird():
    """从 dev_tables.json 导入 BIRD 数据库表结构"""
    import json
    import os

    manifest = os.path.join(
        os.path.dirname(__file__), "..", "..", "resource", "BIRD_SUBSET", "dev_tables.json"
    )
    if not os.path.exists(manifest):
        print("  (BIRD dev_tables.json 不存在，跳过)")
        return

    with open(manifest, "r") as f:
        db_list = json.load(f)

    with get_cursor() as cur:
        for db_info in db_list:
            db_name = db_info["db_id"]
            table_names = db_info["table_names_original"]
            col_names = db_info["column_names_original"]
            col_types = db_info["column_types"]
            pk_list = db_info.get("primary_keys", [])

            # 规范化 PK 为集合
            pk_set = set()
            for pk in pk_list:
                if isinstance(pk, list):
                    for p in pk:
                        pk_set.add(p)
                else:
                    pk_set.add(pk)

            # 创建数据库
            cur.execute(
                "INSERT INTO databases (name, description) VALUES (%s, %s) "
                "ON CONFLICT (name) DO NOTHING RETURNING id",
                (db_name, f"BIRD benchmark — {db_name}"),
            )
            row = cur.fetchone()
            db_id = row["id"] if row else _get_db_id(cur, db_name)

            # 按表分组列
            table_cols: dict[int, list[tuple]] = {}  # table_idx → [(name, type, comment)]
            for i, (t_idx, c_name) in enumerate(col_names):
                if t_idx == -1:
                    continue  # skip "*"
                table_cols.setdefault(t_idx, []).append(
                    (c_name.lower(), BIRD_TYPE_MAP.get(col_types[i], "STRING"), "")
                )

            for t_idx, cols in table_cols.items():
                t_name = table_names[t_idx]
                cur.execute(
                    "INSERT INTO tables (database_id, table_name, table_type, source) "
                    "VALUES (%s, %s, '事实表', 'root') "
                    "ON CONFLICT (database_id, table_name) DO NOTHING RETURNING id",
                    (db_id, t_name),
                )
                t_row = cur.fetchone()
                if not t_row:
                    continue
                table_id = t_row["id"]

                for i, (col_name, col_type, col_comment) in enumerate(cols, 1):
                    cur.execute(
                        "INSERT INTO table_columns "
                        "(table_id, column_name, column_type, column_comment, ordinal_position) "
                        "VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (table_id, column_name) DO NOTHING",
                        (table_id, col_name, col_type, col_comment, i),
                    )

        print(
            f"✓ BIRD 数据就绪 — {len(db_list)} 库 / "
            f"{sum(len(db['table_names_original']) for db in db_list)} 表"
        )
