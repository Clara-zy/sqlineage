# BIRD SUBSET — Financial & Debit Card Specializing

> 本目录是 [BIRD](https://bird-bench.github.io/) (Big Bench for Text-to-SQL) 数据集的一个子集，
> 从 `dev_20240627` 中筛选出 **financial** 和 **debit_card_specializing** 两个数据库的全部数据。

---

> **注意**：`.sqlite` 数据文件（约 100 MB）未纳入 Git 仓库，
> `database_description/` 下的表结构说明仍然保留。需要直接查询数据库时，
> 请从 BIRD 原始数据集获取对应的 `.sqlite` 文件放到原路径。

## 目录结构

```
BIRD_SUBSET/
├── README.md                      # 本文件
├── dev.json                       # 170 条自然语言 → SQL 问答对
├── dev.sql                        # 170 行纯 SQL（含 db_id 标识）
├── dev_tables.json                # 2 套数据库表结构定义
├── dev_tied_append.json           # 3 条 tie-breaker 问题（辅助消歧评估）
└── dev_databases/
    ├── financial/                 # 银行金融数据库 (71 MB)
    │   ├── financial.sqlite
    │   └── database_description/
    │       ├── account.csv        # 账户信息
    │       ├── card.csv           # 信用卡信息
    │       ├── client.csv         # 客户信息
    │       ├── disp.csv           # 账户与客户关联表
    │       ├── district.csv       # 地区信息
    │       ├── loan.csv           # 贷款信息
    │       ├── order.csv          # 订单信息
    │       └── trans.csv          # 交易信息
    └── debit_card_specializing/   # 借记卡消费数据库 (35 MB)
        ├── debit_card_specializing.sqlite
        └── database_description/
            ├── customers.csv      # 客户信息
            ├── gasstations.csv    # 加油站信息
            ├── products.csv       # 产品信息
            ├── transactions_1k.csv # 交易记录
            └── yearmonth.csv      # 年月消费聚合
```

---

## 数据统计

### 总量

| 数据库 | 表数 | 列数 | 问题数 | DB 文件大小 | 领域 |
|--------|------|------|--------|------------|------|
| **financial** | 8 | 55 | **106** | 71 MB | 银行金融 |
| **debit_card_specializing** | 5 | 21 | **64** | 35 MB | 借记卡消费 |
| **合计** | **13** | **76** | **170** | **106 MB** | — |

### 难度分布

| 难度 | financial | debit_card_specializing | 合计 |
|------|-----------|------------------------|------|
| 🟢 simple | 62 | 43 | **105** (61.8%) |
| 🟡 moderate | 37 | 17 | **54** (31.8%) |
| 🔴 challenging | 7 | 4 | **11** (6.4%) |

### Tie-breaker 问题

`dev_tied_append.json` 包含 3 条用于消歧评估的附加问题：

| question_id | 数据库 | 难度 |
|-------------|--------|------|
| 101 | financial | simple |
| 189 | financial | moderate |
| 1517 | debit_card_specializing | simple |

---

## 数据库表结构

### financial（银行金融）

| 表名 | 列数 | 主要字段 |
|------|------|----------|
| `account` | 4 | account_id, district_id, frequency, date |
| `card` | 4 | card_id, disp_id, type, issued |
| `client` | 4 | client_id, gender, birth_date, district_id |
| `disp` | 4 | disp_id, client_id, account_id, type |
| `district` | 16 | district_id, A2~A16 (地区名称、人口、薪资统计等) |
| `loan` | 7 | loan_id, account_id, date, amount, duration, payments, status |
| `order` | 6 | order_id, account_id, bank_to, account_to, amount, k_symbol |
| `trans` | 10 | trans_id, account_id, date, type, operation, amount, balance, k_symbol, bank, account |

### debit_card_specializing（借记卡消费）

| 表名 | 列数 | 主要字段 |
|------|------|----------|
| `customers` | 3 | CustomerID, Segment, Currency |
| `gasstations` | 4 | GasStationID, ChainID, Country, Segment |
| `products` | 2 | ProductID, Description |
| `transactions_1k` | 9 | TransactionID, Date, Time, CustomerID, CardID, GasStationID, ProductID, Amount, Price |
| `yearmonth` | 3 | CustomerID, Date, Consumption |

---

## 数据格式说明

### dev.json — 问答对

每条记录包含：

- `question_id` — 题目编号
- `db_id` — 对应的数据库名（`financial` / `debit_card_specializing`）
- `question` — 自然语言问题
- `evidence` — 领域知识提示（如术语解释、公式说明），部分为空
- `SQL` — 标准 SQL 答案
- `difficulty` — 难度标签（`simple` / `moderate` / `challenging`）

### dev.sql — 纯 SQL 文件

每行格式：`SQL语句\t数据库名`

### dev_tables.json — 表结构定义

遵循 [Spider](https://github.com/taoyds/spider) 的 schema 格式：

- `db_id` — 数据库名
- `table_names_original` / `table_names` — 原始与规范化表名
- `column_names_original` / `column_names` — `[[table_index, column_name], ...]`
- `column_types` — 列类型（`text` / `number` 等）
- `foreign_keys` — 外键约束

---

## 用法

```bash
# Python 读取问答对
import json

with open("BIRD_SUBSET/dev.json") as f:
    data = json.load(f)

# 按数据库过滤
financial_qs = [q for q in data if q["db_id"] == "financial"]
debit_qs     = [q for q in data if q["db_id"] == "debit_card_specializing"]

# 访问 SQLite 数据库（需先从 BIRD 下载 financial.sqlite 放到该路径）
import sqlite3

conn = sqlite3.connect("BIRD_SUBSET/dev_databases/financial/financial.sqlite")
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(cursor.fetchall())
```

---

## 来源

从 BIRD `dev_20240627` 原始数据集中过滤 `db_id` = `financial` 或 `debit_card_specializing` 的条目生成。

- 原始数据集: [bird-bench](https://github.com/AlibabaResearch/DAMO-ConvAI/tree/main/bird)
- 官方页面: [https://bird-bench.github.io/](https://bird-bench.github.io/)
