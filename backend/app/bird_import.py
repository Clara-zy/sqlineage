"""BIRD 测例导入 — 读取 dev.json，预处理 SQL，逐条 build_lineage

用法:
    make bird-import
    或
    env PYTHONPATH=backend backend/.venv/bin/python backend/app/bird_import.py
"""

import json
import os
import re
import sys

from app.database import get_cursor
from app.lineage_engine.engine import build_lineage
from app.lineage_engine.validators import ValidationError
from app.seed import seed

MANIFEST = os.path.join(
    os.path.dirname(__file__), "..", "..", "resource", "BIRD_SUBSET", "dev.json"
)


def preprocess_sql(sql: str) -> str | None:
    """预处理 BIRD SQL，返回处理后的 SQL 或 None（跳过）"""
    # 1. 去掉反引号（`order` → order）
    sql = sql.replace("`", "")

    # 2. 去掉 LIMIT 子句（含子查询中的 LIMIT）
    sql = re.sub(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?", "", sql, flags=re.IGNORECASE)

    # 3. 标识符归一化为小写（兼容 BIRD SQL 的混合大小写）
    #    不处理字符串字面量内部，BIRD 数据集中不存在冲突
    sql = _lowercase_identifiers(sql)

    return sql.strip()


def _lowercase_identifiers(sql: str) -> str:
    """将 SQL 中不在字符串字面量内的标识符转为小写"""
    result = []
    in_string = False
    quote_char = ""
    i = 0
    while i < len(sql):
        ch = sql[i]
        if not in_string and ch in ("'", '"'):
            in_string = True
            quote_char = ch
            result.append(ch)
        elif in_string and ch == quote_char:
            in_string = False
            result.append(ch)
        elif in_string:
            result.append(ch)
        else:
            result.append(ch.lower())
        i += 1
    return "".join(result)


def main():
    seed()

    manifest = os.path.abspath(MANIFEST)
    if not os.path.exists(manifest):
        print(f"✗ 文件不存在: {manifest}")
        sys.exit(1)

    with open(manifest, "r") as f:
        data = json.load(f)

    # 清空旧的 BIRD 构建历史
    with get_cursor() as cur:
        cur.execute("DELETE FROM build_history WHERE comment LIKE %s", ("[BIRD]%",))

    success = 0
    skipped = 0
    failed = 0
    errors: dict[str, int] = {}

    for item in data:
        qid = item["question_id"]
        db_id = item["db_id"]
        difficulty = item["difficulty"]
        question = item["question"]
        sql = item["SQL"]

        processed = preprocess_sql(sql)
        if processed is None:
            skipped += 1
            errors["CURRENT_TIMESTAMP"] = errors.get("CURRENT_TIMESTAMP", 0) + 1
            continue

        # 如果去掉了 LIMIT 但 SQL 没变 → 原本就没有 LIMIT
        had_limit = (processed != sql.replace("`", ""))

        comment = f"[BIRD] {db_id}/{difficulty} Q{qid}: {question[:50]}"

        try:
            result = build_lineage(processed, comment=comment)
            new_f = result.get("new_fields", 0)
            new_sq = result.get("new_subqueries", 0)
            limit_tag = " (已去LIMIT)" if had_limit and "LIMIT" in sql.upper() else ""
            print(f"  ✓ Q{qid:<4} [{difficulty:<11}] {question[:45]}...{limit_tag}")
            if new_f > 0 or new_sq > 0:
                print(f"        +{new_f}字段 +{new_sq}SQ")
            success += 1
        except ValidationError as e:
            print(f"  ⊘ Q{qid:<4} [{difficulty}] 校验拒绝 [{e.code}] {e.message[:50]}")
            skipped += 1
            errors[f"E{e.code}"] = errors.get(f"E{e.code}", 0) + 1
        except Exception as e:
            msg = str(e)[:60]
            print(f"  ✗ Q{qid:<4} [{difficulty}] {msg}")
            failed += 1
            key = type(e).__name__
            errors[key] = errors.get(key, 0) + 1

    print(f"\n{'='*55}")
    print(f"总计: {len(data)}  成功: {success}  跳过: {skipped}  失败: {failed}")
    if errors:
        print(f"错误分布: {errors}")


if __name__ == "__main__":
    main()
