"""从数据标准平台导出的列清单 + 人工补充的表结构，导入 tables / table_columns

数据源 1: resource/UP_SUBSET/platform_schema/schema_*.json
    每个文件是一张表的列清单，关键字段:
        objName        表名          e.g. TBL_BSCOM_TRANS_MIDDLE_BASE_D_SCL
        schemaName     库名          e.g. BS_COMDB（个别为空，按表名前缀兜底）
        colName        物理列名      e.g. trans_id
        colCnname      中文名        e.g. 交易流水号
        sourceColType  Hive 类型     STRING / BIGINT / DECIMAL(16,0)...（部分为「不涉及」/空）
        colIndex       列序号 1..N   ⚠️ 是平台目录顺序，**不等于物理列序**

数据源 2: resource/UP_SUBSET/manual_supplement/*.json（平台未登记的表，人工补充）
    结构: {库名, 表名, 表含义, 字段清单:[{字段名, 字段类型, 字段含义, 取值样例}]}
    example.json 是样板，跳过

运行: make platform-import
"""

import glob
import json
import os

from app.database import get_cursor

SCHEMA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "resource", "UP_SUBSET", "platform_schema"
)
SUPPLEMENT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "resource", "UP_SUBSET", "manual_supplement"
)

# 平台导出缺 schemaName 的表 → 按表名前缀推断所属库
DB_HINTS = {
    "VIW_BK000_": "bk_000db",
    "TBL_BK000_": "bk_000db",
    "TBL_BKDSJ_": "bk_dsjdb",
}

# 构建目标表（sink）；其余表一律视为血缘起点（root）
SINK_TABLES = {"tbl_bscom_trans_middle_base_d_scl"}

# 平台标记为「不涉及」/空 的类型 → 兜底
FALLBACK_TYPE = "STRING"
UNKNOWN_TYPES = {"不涉及", "", None}

# 人工补充文档里的列名笔误 → 统一到 SQL / 平台惯用拼写（intnl_*）
# 左边是文档写法，右边是实际列名；确认后可删除本表
COLUMN_NAME_FIXES = {
    "tbl_bk000_bkdsj_dim_intnlcdnm": {
        "inntl_org_cd": "intnl_org_cd",
        "inntl_org_nm": "intnl_org_nm",
    },
    "tbl_bkdsj_dm_profit_dim_gov_m_dtl": {
        "inntl_org_nm": "intnl_org_nm",
    },
}

# 平台导出缺失、但被 SQL 引用的列 → 临时补登（确认后请以真实 DDL 为准）
#   表名: [(列名, 类型, 说明)]
EXTRA_COLUMNS = {
    "tbl_bscom_achis_ma_cups_trans_dtl": [
        ("act_id", "STRING", "活动编号(20位)。平台导出无此列，落自 SQL 的 on a.act_id 引用"),
    ],
}

# ── 输出表列清单校正（业务已裁决：以 v24 SQL 投影为准）──
SINK_TABLE = "tbl_bscom_trans_middle_base_d_scl"

# SQL 有 / 平台无 → 补进表结构
SINK_ADD_COLUMNS = [
    ("app_ins_id_cd", "STRING", "app代码"),
    ("domin_nm2_changzai", "STRING", "常在地分公司-地市"),
    ("intnl_org_nm_changzai", "STRING", "常在地分公司"),
    ("measure_map", "map<string,bigint>", "度量集合"),
    ("online_type2", "STRING", "线上类型2"),
    ("product_type_map", "map<string,string>", "产品类型集合"),
    ("qrhis_pay_ctrl", "STRING", "二维码支付控制类型"),
]

# 平台有 / SQL 无 → 从表结构剔除
SINK_DROP_COLUMNS = [
    "domin_id2_104", "domin_id2_32", "domin_id2_changzai", "domin_id2_qr",
    "domin_id3_104", "domin_id3_32", "domin_id3_qr",
]

# 用 SQL 投影顺序重排 sink 列序（平台 colIndex 是目录序，不是物理列序）
SINK_CLEAN_SQL = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "resource", "UP_SUBSET", "query", "经营中间表result250709.clean.sql",
)


def _table_type(table_name: str) -> str:
    if "_DIM_" in table_name or table_name.startswith(("dim_", "viw_")):
        return "维度表"
    if "MCHNT_INF" in table_name:
        return "维度表"
    if "MIDDLE" in table_name:
        return "中间表"
    if "STAT" in table_name:
        return "汇总表"
    return "事实表"


def _db_name(row: dict) -> str:
    schema = (row.get("schemaName") or "").strip()
    if schema:
        return schema.lower()
    obj = (row.get("objName") or "").upper()
    for prefix, db in DB_HINTS.items():
        if obj.startswith(prefix):
            return db
    return "bs_comdb"


def load_tables() -> list[dict]:
    """读取平台导出 + 人工补充 → [{db, table, table_type, source, columns:[...]}]"""
    return load_platform() + load_supplement()


def load_platform() -> list[dict]:
    """数据源 1：平台导出的 schema_*.json"""
    tables = []
    for path in sorted(glob.glob(os.path.join(SCHEMA_DIR, "schema_*.json"))):
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("root") or []
        if not rows:
            print(f"  ⚠️ {os.path.basename(path)} 无列数据，跳过")
            continue

        head = rows[0]
        table_name = (head["objName"] or "").lower()
        db_name = _db_name(head)
        columns = []
        for r in sorted(rows, key=lambda x: x["colIndex"]):
            col_type = r.get("sourceColType")
            if col_type in UNKNOWN_TYPES:
                col_type = FALLBACK_TYPE
            columns.append({
                "name": (r["colName"] or "").lower(),
                "type": str(col_type)[:64],
                "comment": (r.get("colCnname") or "")[:512],
                "ordinal": r["colIndex"],
            })
        tables.append({
            "db": db_name,
            "table": table_name,
            "table_type": _table_type(table_name.upper()),
            "source": "sink" if table_name in SINK_TABLES else "root",
            "columns": columns,
            "file": os.path.basename(path),
        })
    return tables


def load_supplement() -> list[dict]:
    """数据源 2：人工补充的表结构（平台未登记的表）"""
    tables = []
    for path in sorted(glob.glob(os.path.join(SUPPLEMENT_DIR, "*.json"))):
        if os.path.basename(path) == "example.json":
            continue
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        db_name = (payload.get("库名") or "").strip().lower()
        table_name = (payload.get("表名") or "").strip().lower()
        if not db_name or not table_name:
            print(f"  ⚠️ {os.path.basename(path)} 缺库名/表名，跳过")
            continue

        fixes = COLUMN_NAME_FIXES.get(table_name, {})
        columns = []
        for i, c in enumerate(payload.get("字段清单") or [], 1):
            raw_name = (c.get("字段名") or "").strip().lower()
            if not raw_name:
                continue
            name = fixes.get(raw_name, raw_name)
            comment = (c.get("字段含义") or "")[:512]
            if name != raw_name:
                comment = f"{comment}（补充文档写作 {raw_name}，已按 SQL/平台拼写归一）"[:512]
            columns.append({
                "name": name,
                "type": str(c.get("字段类型") or FALLBACK_TYPE)[:64],
                "comment": comment,
                "ordinal": i,
            })
        tables.append({
            "db": db_name,
            "table": table_name,
            "table_type": _table_type(table_name.upper()),
            "source": "sink" if table_name in SINK_TABLES else "root",
            "columns": columns,
            "file": os.path.basename(path),
        })
    return tables


def apply_extra_columns(tables: list[dict]) -> None:
    """把 EXTRA_COLUMNS 补登到对应表（追加在末尾，不改已有列序）"""
    by_name = {t["table"]: t for t in tables}
    for table, extras in EXTRA_COLUMNS.items():
        t = by_name.get(table)
        if not t:
            print(f"  ⚠️ EXTRA_COLUMNS 目标表不存在，跳过: {table}")
            continue
        have = {c["name"] for c in t["columns"]}
        ordinal = max((c["ordinal"] for c in t["columns"]), default=0)
        for name, col_type, comment in extras:
            if name in have:
                continue
            ordinal += 1
            t["columns"].append({
                "name": name,
                "type": col_type,
                "comment": comment[:512],
                "ordinal": ordinal,
            })
            print(f"  + 补登列 {table}.{name} ({col_type})")


def _sink_projection_order() -> list[str] | None:
    """从 clean SQL 读出输出表的投影列名（按投影顺序 = 物理列序）"""
    if not os.path.exists(SINK_CLEAN_SQL):
        return None
    import sqlglot

    text = open(SINK_CLEAN_SQL, encoding="utf-8").read().split("\n")
    start = next(
        (i for i, ln in enumerate(text) if ln.strip().lower().startswith("insert ")), None
    )
    if start is None:
        return None
    ast = sqlglot.parse_one("\n".join(text[start:]), dialect="hive")
    names = []
    for e in ast.expression.expressions:
        if isinstance(e, sqlglot.exp.Alias):
            names.append(e.alias.lower())
        elif isinstance(e, sqlglot.exp.Column):
            names.append(e.name.lower())
        else:
            return None  # 出现无名投影，无法用列名对齐
    return names


def apply_sink_alignment(tables: list[dict]) -> list[str]:
    """输出表列清单校正：补 7 列、剔 7 列、按 SQL 投影顺序重排列序。

    返回需要从库里物理删除的列名。
    """
    t = next((x for x in tables if x["table"] == SINK_TABLE), None)
    if not t:
        return []

    dropped = set(SINK_DROP_COLUMNS)
    before = len(t["columns"])
    t["columns"] = [c for c in t["columns"] if c["name"] not in dropped]
    removed = before - len(t["columns"])

    have = {c["name"] for c in t["columns"]}
    added = 0
    for name, col_type, comment in SINK_ADD_COLUMNS:
        if name in have:
            continue
        t["columns"].append({
            "name": name, "type": col_type, "comment": comment[:512], "ordinal": 0,
        })
        have.add(name)
        added += 1
    print(f"  输出表列校正：补 {added} 列 / 剔除 {removed} 列 → {len(t['columns'])} 列")

    order = _sink_projection_order()
    if not order:
        print("  ⚠️ 未能读取 SQL 投影顺序，列序保持平台原样")
        return sorted(dropped)

    names = {c["name"] for c in t["columns"]}
    missing = [n for n in order if n not in names]
    extra = sorted(names - set(order))
    if missing or extra:
        print(f"  ⚠️ 列名与 SQL 投影不一致，跳过列序对齐。缺: {missing} 多: {extra}")
        return sorted(dropped)

    pos = {n: i for i, n in enumerate(order, 1)}
    for c in t["columns"]:
        c["ordinal"] = pos[c["name"]]
    print(f"  ✓ sink 列序已对齐 SQL 投影（1..{len(order)}）")
    return sorted(dropped)


def import_all():
    tables = load_tables()
    if not tables:
        print("未找到 schema 文件:", SCHEMA_DIR)
        return
    apply_extra_columns(tables)
    dropped_columns = apply_sink_alignment(tables)

    db_ids: dict[str, int] = {}
    total_cols = 0
    with get_cursor() as cur:
        for t in tables:
            if t["db"] not in db_ids:
                cur.execute(
                    "INSERT INTO databases (name, description) VALUES (%s, %s) "
                    "ON CONFLICT (name) DO NOTHING RETURNING id",
                    (t["db"], "数据标准平台导入（UP_SUBSET）"),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute("SELECT id FROM databases WHERE name = %s", (t["db"],))
                    row = cur.fetchone()
                db_ids[t["db"]] = row["id"]

            cur.execute(
                "INSERT INTO tables (database_id, table_name, logical_name, table_type, source) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (database_id, table_name) DO UPDATE "
                "SET table_type = EXCLUDED.table_type, source = EXCLUDED.source, "
                "    updated_at = now() "
                "RETURNING id",
                (db_ids[t["db"]], t["table"], t["table"], t["table_type"], t["source"]),
            )
            table_id = cur.fetchone()["id"]

            # 列序重排：先把旧 ordinal 挪到负数区间，避免 (table_id, ordinal_position) 唯一冲突
            if t["table"] == SINK_TABLE and dropped_columns:
                cur.execute(
                    "UPDATE table_columns SET ordinal_position = -ordinal_position "
                    "WHERE table_id = %s AND ordinal_position > 0",
                    (table_id,),
                )

            for c in t["columns"]:
                cur.execute(
                    "INSERT INTO table_columns "
                    "(table_id, column_name, column_type, column_comment, ordinal_position) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (table_id, column_name) DO UPDATE "
                    "SET column_type = EXCLUDED.column_type, "
                    "    column_comment = EXCLUDED.column_comment, "
                    "    ordinal_position = EXCLUDED.ordinal_position, "
                    "    updated_at = now()",
                    (table_id, c["name"], c["type"], c["comment"], c["ordinal"]),
                )
            total_cols += len(t["columns"])

            # 输出表：把剔除的列从库里真正删掉（先清映射指针/历史，避免外键悬空）
            if t["table"] == SINK_TABLE and dropped_columns:
                cur.execute(
                    "SELECT id FROM table_columns WHERE table_id = %s AND column_name = ANY(%s)",
                    (table_id, dropped_columns),
                )
                stale = [r["id"] for r in cur.fetchall()]
                if stale:
                    cur.execute(
                        "DELETE FROM table_column_mappings WHERE table_column_id = ANY(%s)",
                        (stale,),
                    )
                    cur.execute("DELETE FROM table_columns WHERE id = ANY(%s)", (stale,))
                    print(f"  已从库里删除 {len(stale)} 个被剔除的列")

    by_db: dict[str, int] = {}
    for t in tables:
        by_db[t["db"]] = by_db.get(t["db"], 0) + 1
    print(f"✓ 表结构导入完成：{len(tables)} 张表 / {total_cols} 列")
    for db, cnt in sorted(by_db.items()):
        print(f"    {db}: {cnt} 张表")


if __name__ == "__main__":
    import_all()
