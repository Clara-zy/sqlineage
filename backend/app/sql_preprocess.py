"""生产 Hive SQL 清洗：租户库 SQL → 可入血缘的标准化 SQL

处理的都是「与业务语义无关的机械问题」：
  1. 剥离 create temporary function（Hive UDF 注册语句，不是血缘结构）
  2. 清除零宽字符 U+200B/U+200C/U+200D/U+FEFF
  3. 修语法缺陷（缺运算符 / 重复 on / GROUP BY 残留别名 / UDF 前误加表别名 / 列名笔误）
  4. 租户表名 → 平台表名或人工补充表名（见 TABLE_MAP）

清洗结果写入 *.clean.sql，文件头自动带上改动清单，便于审阅与回归比对。

用法:
    make preprocess-sql          # 生成 .clean.sql 并做 hive/spark 解析校验
    或 backend/.venv/bin/python backend/app/sql_preprocess.py [--check]
"""

import os
import re
import sys

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SRC = os.path.join(_ROOT, "resource", "UP_SUBSET", "query", "经营中间表result250709.sql")
OUT = os.path.join(_ROOT, "resource", "UP_SUBSET", "query", "经营中间表result250709.clean.sql")

# 租户表名 → 平台表名 / 人工补充表名
TABLE_MAP = {
    "bk_000db.viw_bk000_bisvc_dim_intnl_domin": "bs_comdb.tbl_bscom_dim_intnl_domin",
    "bk_000db.tbl_bk000_bisvc_dim_ins": "bs_comdb.tbl_bscom_dim_ins",
    "bk_000db.viw_bk000_bisvc_dim_trans": "bs_comdb.tbl_bscom_dim_trans",
    "bk_000db.viw_bk000_bisvc_dim_all_chnl_mchnt_branch":
        "bs_comdb.tbl_bscom_dim_all_chnl_mchnt_branch",
    "bk_000db.tbl_bk000_bisvc_achis_ma_cups_trans_d_dtl":
        "bs_comdb.tbl_bscom_achis_ma_cups_trans_dtl",
    "bk_000db.viw_bk000_dim_ins_app_tp_qr_01244264": "bk_000db.viw_bk000_dim_ins_app_tp_qr",
    # 输出表：v24 sink → 平台登记的 trans_middle_base
    "bk_dsjdb.tbl_bkdsj_jyzb2023_trans_middle_v24_d_scl_01245264":
        "bs_comdb.tbl_bscom_trans_middle_base_d_scl",
    # manual_supplement 补充的 8 张表：去掉租户后缀，对齐补充文档表名
    "bk_000db.viw_bk000_yjhx_redeem_stat_day_scl_01245264":
        "bk_000db.viw_bk000_yjhx_redeem_stat_day_scl",
    "bk_000db.viw_bk000_card_changzai_locus_01245264":
        "bk_000db.viw_bk000_card_changzai_locus",
    "bk_dsjdb.tbl_bkdsj_smoke_mchnt_m_scl_01244264": "bk_dsjdb.tbl_bkdsj_smoke_mchnt_m_scl",
    "bk_dsjdb.tbl_bkdsj_mchnt_info_fragment_bucket_y_scl_01245264":
        "bk_dsjdb.tbl_bkdsj_mchnt_info_fragment_bucket_y_scl",
    "bk_dsjdb.tbl_bkdsj_dm_profit_dim_gov_m_dtl_01245264":
        "bk_dsjdb.tbl_bkdsj_dm_profit_dim_gov_m_dtl",
    "bk_dsjdb.tbl_bkdsj_dm_profit_dim_appid_m_dtl_01244264":
        "bk_dsjdb.tbl_bkdsj_dm_profit_dim_appid_m_dtl",
    "bk_dsjdb.tbl_bkdsj_biysf_tmj_life_mchnt_d_scl_01245264":
        "bk_dsjdb.tbl_bkdsj_biysf_tmj_life_mchnt_d_scl",
}

# 语法缺陷修复：(原片段, 新片段, 说明, 命中模式)
#   "one"  必须恰好命中 1 处（对不上就报警不动，避免误改）
#   "last" 只改最后一处（投影里同名别名合法，只有 GROUP BY 段那段拷贝要改）
#   "all"  全改
FIXES = [
    (
        "nvl(r008_cups_cret_disc_at,0)-nvl(r008_cups_debt_disc_at,0)\n"
        "            nvl(r008_fwd_branch_cret_disc_at,0)-nvl(r008_fwd_branch_debt_disc_at,0)+",
        "nvl(r008_cups_cret_disc_at,0)-nvl(r008_cups_debt_disc_at,0)+\n"
        "            nvl(r008_fwd_branch_cret_disc_at,0)-nvl(r008_fwd_branch_debt_disc_at,0)+",
        "两个 nvl 之间补 `+`",
        "one",
    ),
    (") xwk_issdomin on on substr(", ") xwk_issdomin on substr(", "JOIN 条件去掉重复 `on`", "one"),
    (
        ",get_json_object(qrhis_bus_info,'$.redirect_no') as bus_info_redirect_no",
        ",get_json_object(qrhis_bus_info,'$.redirect_no')",
        "GROUP BY 段去掉残留别名 bus_info_redirect_no",
        "last",
    ),
    (
        ",get_json_object(qrhis_bus_info,'$.ua') as bus_info_ua",
        ",get_json_object(qrhis_bus_info,'$.ua')",
        "GROUP BY 段去掉残留别名 bus_info_ua",
        "last",
    ),
    # achis_fied 是 achis_field 的笔误（cups 表只有 achis_field，SQL 其余 8 处也写作 achis_field）
    ("get_json_object(achis_fied,", "get_json_object(achis_field,",
     "achis_fied → achis_field（笔误）", "all"),
    # L1 把该聚合命名为 exch_disc_at，外层 measure_map 却引用 exch_dif_disc_at；统一到 map 的键
    ("        ) as exch_disc_at", "        ) as exch_dif_disc_at",
     "L1 别名 exch_disc_at → exch_dif_disc_at", "one"),
    # 函数前误加了表别名：ON a.unhexDEC(...) → ON unhexDEC(...)
    ("on a.unhexDEC(", "on unhexDEC(", "去掉函数前的多余表别名 a.", "one"),
]

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)

HEADER = """\
-- ============================================================================
-- 清洗版：trans_middle_base（v24）sink 逻辑
-- 来源：经营中间表result250709.sql（原始 4611 行）
-- 生成：backend/app/sql_preprocess.py（make preprocess-sql 可复跑）
--
-- 改动清单：
--   [1] 剥离 4 条 create temporary function（Hive UDF 注册，非血缘结构）
--   [2] 清除零宽字符 U+200B（原 464/465/1563/1564/3897/3898 行，共 16 处）
--   [3] 补一个缺失的 `+`（原 2345-2346 行，sum(... AS cross_at) 内）
--   [4] 去掉重复的 `on`（原 2955 行）
--   [5] GROUP BY 段去掉 2 处残留 `as` 别名（原 3897-3898 行）
--   [6] 租户表名 → 平台表名 / 人工补充表名（15 组，见下）
--   [7] achis_fied → achis_field（笔误，2 处）
--   [8] L1 别名 exch_disc_at → exch_dif_disc_at（对齐外层 measure_map 的键）
--   [9] ON a.unhexDEC(...) → ON unhexDEC(...)（函数前误加表别名）
--
-- 表结构侧配套（make platform-import）：
--   * 输出表 tbl_bscom_trans_middle_base_d_scl：按 v24 投影补 7 列、剔 7 列、
--     列序按投影顺序重排（平台 colIndex 是目录序，不是物理列序）
--   * 主表 tbl_bscom_achis_ma_cups_trans_dtl：补登 act_id（平台导出缺，被 on 引用）
--   * 补充文档里 inntl_org_cd/nm 疑为 intnl_org_cd/nm 笔误，已按 SQL 拼写归一
--
-- 表名映射：
--   bk_000db.viw_bk000_bisvc_dim_intnl_domin    → bs_comdb.tbl_bscom_dim_intnl_domin
--   bk_000db.tbl_bk000_bisvc_dim_ins            → bs_comdb.tbl_bscom_dim_ins
--   bk_000db.viw_bk000_bisvc_dim_trans          → bs_comdb.tbl_bscom_dim_trans
--   bk_000db.viw_bk000_bisvc_dim_all_chnl_mchnt_branch → bs_comdb.tbl_bscom_dim_all_chnl_mchnt_branch
--   bk_000db.tbl_bk000_bisvc_achis_ma_cups_trans_d_dtl → bs_comdb.tbl_bscom_achis_ma_cups_trans_dtl
--   bk_000db.viw_bk000_dim_ins_app_tp_qr_01244264 → bk_000db.viw_bk000_dim_ins_app_tp_qr
--   bk_dsjdb.tbl_bkdsj_jyzb2023_trans_middle_v24_d_scl_01245264 → bs_comdb.tbl_bscom_trans_middle_base_d_scl
--   bk_000db.viw_bk000_yjhx_redeem_stat_day_scl_01245264 → bk_000db.viw_bk000_yjhx_redeem_stat_day_scl
--   bk_000db.viw_bk000_card_changzai_locus_01245264 → bk_000db.viw_bk000_card_changzai_locus
--   bk_dsjdb.tbl_bkdsj_smoke_mchnt_m_scl_01244264 → bk_dsjdb.tbl_bkdsj_smoke_mchnt_m_scl
--   bk_dsjdb.tbl_bkdsj_mchnt_info_fragment_bucket_y_scl_01245264 → bk_dsjdb.tbl_bkdsj_mchnt_info_fragment_bucket_y_scl
--   bk_dsjdb.tbl_bkdsj_dm_profit_dim_gov_m_dtl_01245264 → bk_dsjdb.tbl_bkdsj_dm_profit_dim_gov_m_dtl
--   bk_dsjdb.tbl_bkdsj_dm_profit_dim_appid_m_dtl_01244264 → bk_dsjdb.tbl_bkdsj_dm_profit_dim_appid_m_dtl
--   bk_dsjdb.tbl_bkdsj_biysf_tmj_life_mchnt_d_scl_01245264 → bk_dsjdb.tbl_bkdsj_biysf_tmj_life_mchnt_d_scl
-- ============================================================================

"""


def clean(text: str) -> tuple[str, list[str]]:
    report: list[str] = []

    # 1. 零宽字符（先做，避免影响后续匹配）
    n_zw = sum(text.count(ch) for ch in "\u200b\u200c\u200d\ufeff")
    text = text.translate(ZERO_WIDTH)
    report.append(f"清除零宽字符: {n_zw} 处")

    # 2. 剥离 create temporary function
    kept = []
    dropped = 0
    for ln in text.split("\n"):
        if re.match(r"^\s*create\s+temporary\s+function\b", ln, re.I):
            dropped += 1
            kept.append("-- [已剥离] " + ln.strip())
            continue
        kept.append(ln)
    text = "\n".join(kept)
    report.append(f"剥离 create temporary function: {dropped} 条（保留为注释）")

    # 3. 语法缺陷
    for old, new, desc, mode in FIXES:
        cnt = text.count(old)
        if cnt == 0 or (mode == "one" and cnt != 1):
            report.append(f"⚠️ {desc}: 命中 {cnt} 次，未修改")
            continue
        if mode == "last":
            head, _, tail = text.rpartition(old)
            text = head + new + tail
            report.append(f"修复: {desc}（共 {cnt} 处，只改最后一处）")
        else:
            text = text.replace(old, new)
            report.append(f"修复: {desc}")

    # 4. 表名替换（长名优先，避免前缀互相截断）
    for tenant, platform in sorted(TABLE_MAP.items(), key=lambda kv: -len(kv[0])):
        pattern = re.compile(r"(?<![\w.])" + re.escape(tenant) + r"(?![\w])", re.I)
        text, cnt = pattern.subn(platform, text)
        report.append(f"表名: {tenant} → {platform}  ({cnt} 处)")

    return text, report


def main():
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    out, report = clean(text)
    for line in report:
        print("  " + line)

    body = HEADER + out.lstrip("\n")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"\n✓ 写出 {os.path.relpath(OUT, _ROOT)}（{body.count(chr(10)) + 1} 行）")

    if "--check" in sys.argv:
        import sqlglot

        lines = body.split("\n")
        start = next(
            (i for i, ln in enumerate(lines) if ln.strip().lower().startswith("insert ")),
            None,
        )
        if start is None:
            print("✗ 未找到 insert 语句")
            return
        stmt = "\n".join(lines[start:])
        for dialect in ("hive", "spark"):
            try:
                ast = sqlglot.parse_one(stmt, dialect=dialect)
                print(f"✓ [{dialect}] 解析通过，顶层 = {type(ast).__name__}")
            except Exception as e:  # noqa: BLE001
                print(f"✗ [{dialect}] 解析失败: {str(e).splitlines()[0]}")


if __name__ == "__main__":
    main()
