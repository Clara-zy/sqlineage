# SQLineage SQL 测试样例集

> 方言: Hive | 用途: 血缘引擎渐进式开发的测试用例

---

## 1. 表结构设计 (9 张表)

### 表1: `trans_flow` — 交易流水表 (source=root, 事实表)

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | trans_id | STRING | 交易流水号 |
| 2 | mchnt_cd | STRING | 商户号 |
| 3 | usr_id | STRING | 用户ID |
| 4 | trans_at | DECIMAL(18,2) | 交易金额(元) |
| 5 | trans_time | STRING | 交易时间 yyyy-MM-dd HH:mm:ss |
| 6 | trans_status | STRING | SUCCESS / FAIL / REFUND |
| 7 | region_cd | STRING | 交易发生地区划代码 |

### 表2: `dim_mchnt` — 商户注册表 (source=root, 维度表)

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | mchnt_cd | STRING | 商户号(主键) |
| 2 | mchnt_nm | STRING | 商户名称 |
| 3 | industry_id | STRING | 行业ID |
| 4 | industry_list | STRING | 所属行业列表(逗号分隔)，用于 LATERAL VIEW 测试 |
| 5 | region_cd | STRING | 注册地区划代码 |
| 6 | register_time | STRING | 注册时间 |

### 表3: `dim_usr` — 用户注册表 (source=root, 维度表)

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | usr_id | STRING | 用户ID(主键) |
| 2 | usr_nm | STRING | 用户昵称 |
| 3 | gender | STRING | M / F |
| 4 | usr_type | STRING | NORMAL / VIP |
| 5 | register_time | STRING | 注册时间 |

### 表4: `dim_industry` — 行业维度表 (source=root, 维度表)

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | industry_id | STRING | 行业ID(主键) |
| 2 | industry_nm | STRING | 行业名称 |
| 3 | category | STRING | 一级分类 |

### 表5: `dim_domin` — 行政区划表 (source=root, 维度表)

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | region_cd | STRING | 区划代码(主键) |
| 2 | region_nm | STRING | 区划名称 |
| 3 | parent_region_cd | STRING | 上级区划代码 |

### 表6: `usr_behave_bury` — 用户行为埋点 (source=root, 事实表)

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | event_id | STRING | 事件ID |
| 2 | usr_id | STRING | 用户ID |
| 3 | mchnt_cd | STRING | 浏览的商户号 |
| 4 | behave_type | STRING | CLICK / VIEW / SEARCH / FAV / SHARE |
| 5 | behave_time | STRING | 行为时间 |

### 表7: `dws_mchnt_day` — 商户日汇总表 (source=middle, 汇总表)

由 Q12 的查询写入生成。

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | mchnt_cd | STRING | 商户号 |
| 2 | stat_date | STRING | 统计日期 yyyyMMdd |
| 3 | trans_cnt | BIGINT | 总交易笔数 |
| 4 | trans_at | DECIMAL(18,2) | 总交易金额 |
| 5 | succ_cnt | BIGINT | 成功交易笔数 |

---

## 2. 查询清单 (15条)

### Q1 — 统计一段时间内成功的总交易金额
> Layer: L0 + L2 | 单表 + WHERE + 聚合

```sql
SELECT
    sum(trans_at) AS total_at
FROM trans_flow
WHERE trans_status = 'SUCCESS'
  AND trans_time >= '2026-06-01 00:00:00'
  AND trans_time < '2026-07-01 00:00:00'
```

### Q2 — 统计一段时间内分地区的成功交易笔数
> Layer: L2 | 两表LEFT JOIN + 聚合

```sql
SELECT
    b.region_nm,
    count(1) AS trans_cnt
FROM trans_flow a
LEFT JOIN dim_domin b ON a.region_cd = b.region_cd
WHERE a.trans_status = 'SUCCESS'
  AND a.trans_time >= '2026-06-01 00:00:00'
  AND a.trans_time < '2026-07-01 00:00:00'
GROUP BY b.region_nm
```

### Q3 — 统计一段时间内酒店行业的退款交易笔数
> Layer: L3 + L5 | 3表LEFT JOIN + 聚合

```sql
SELECT
    c.industry_nm,
    count(1) AS refund_cnt,
    sum(a.trans_at) AS refund_at
FROM trans_flow a
LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
LEFT JOIN dim_industry c ON b.industry_id = c.industry_id
WHERE a.trans_status = 'REFUND'
  AND c.industry_nm = '酒店'
  AND a.trans_time >= '2026-06-01 00:00:00'
  AND a.trans_time < '2026-07-01 00:00:00'
GROUP BY c.industry_nm
```

### Q4 — 统计一段时间内多行为用户发生交易的占比
> Layer: L4 + L6 | 子查询 + 组合条件(AND/OR/IN/NOT)+ 除法

```sql
SELECT
    count(DISTINCT t.usr_id) AS trans_usr_cnt,
    b.total_usr_cnt,
    round(count(DISTINCT t.usr_id) / b.total_usr_cnt, 4) AS ratio
FROM trans_flow t
CROSS JOIN (
    -- 统计符合复杂条件的用户总数
    SELECT count(DISTINCT bu.usr_id) AS total_usr_cnt
    FROM usr_behave_bury bu
    LEFT JOIN dim_usr u ON bu.usr_id = u.usr_id
    WHERE bu.behave_time >= '2026-06-01 00:00:00'
      AND bu.behave_time < '2026-07-01 00:00:00'
      AND (
          -- 女性VIP用户的行为
          (u.gender = 'F' AND u.usr_type = 'VIP')
          OR
          -- 或男性用户在早间时段的行为
          (u.gender = 'M' AND substring(bu.behave_time, 12, 2) < '09')
      )
      AND bu.behave_type IN ('CLICK', 'VIEW', 'SEARCH', 'FAV', 'SHARE')
      AND bu.mchnt_cd NOT IN ('M9999')  -- 排除测试商户
) b
WHERE t.trans_time >= '2026-06-01 00:00:00'
  AND t.trans_time < '2026-07-01 00:00:00'
  AND t.trans_status = 'SUCCESS'
  AND (t.trans_at > 0 OR t.trans_at IS NOT NULL)
  AND t.usr_id IN (
      SELECT bu2.usr_id
      FROM usr_behave_bury bu2
      LEFT JOIN dim_usr u2 ON bu2.usr_id = u2.usr_id
      WHERE bu2.behave_time >= '2026-06-01 00:00:00'
        AND bu2.behave_time < '2026-07-01 00:00:00'
        AND bu2.behave_type IN ('CLICK', 'VIEW', 'SEARCH', 'FAV', 'SHARE')
        AND u2.usr_type = 'NORMAL'
      GROUP BY bu2.usr_id
      HAVING count(DISTINCT bu2.behave_type) >= 3
  )
```

**组合条件覆盖**: AND / OR / IN / NOT IN / IS NOT NULL / substring / 子查询条件

### Q5 — 单表表达式 (nvl, round, concat)
> Layer: L1 | 单表复杂表达式

```sql
SELECT
    trans_id,
    nvl(mchnt_cd, 'UNKNOWN') AS mchnt_cd,
    round(trans_at / 100, 2) AS trans_at_yuan,
    concat(trans_status, '_', CAST(region_cd AS STRING)) AS status_region
FROM trans_flow
```

### Q6 — 多聚合 + GROUP BY
> Layer: L2 | 多聚合函数 + 分组

```sql
SELECT
    mchnt_cd,
    sum(trans_at) AS total_at,
    count(1) AS total_cnt,
    avg(trans_at) AS avg_at,
    max(trans_at) AS max_at,
    min(trans_at) AS min_at
FROM trans_flow
WHERE trans_status = 'SUCCESS'
GROUP BY mchnt_cd
```

### Q7 — 子查询
> Layer: L4 | 嵌套子查询

```sql
SELECT
    a.mchnt_cd,
    a.total_cnt,
    a.total_at
FROM (
    SELECT
        mchnt_cd,
        count(1) AS total_cnt,
        sum(trans_at) AS total_at
    FROM trans_flow
    WHERE trans_status = 'SUCCESS'
    GROUP BY mchnt_cd
) a
WHERE a.total_cnt > 10
```

### Q8 — CASE WHEN
> Layer: L6 | 条件表达式

```sql
SELECT
    trans_id,
    trans_at,
    CASE
        WHEN trans_at > 10000 THEN '大额'
        WHEN trans_at > 1000  THEN '中额'
        WHEN trans_at > 100   THEN '小额'
        ELSE '微额'
    END AS amt_level,
    trans_status
FROM trans_flow
```

### Q9 — UNION ALL
> Layer: L7 | 合并查询

```sql
SELECT
    mchnt_cd,
    'day' AS stat_level,
    stat_date AS stat_time,
    trans_cnt,
    trans_at
FROM dws_mchnt_day
WHERE stat_date >= '20260601'

UNION ALL

SELECT
    mchnt_cd,
    'month' AS stat_level,
    substring(stat_date, 1, 6) AS stat_time,
    sum(trans_cnt) AS trans_cnt,
    sum(trans_at) AS trans_at
FROM dws_mchnt_day
WHERE stat_date >= '20260601'
GROUP BY mchnt_cd, substring(stat_date, 1, 6)
```

### Q10 — 窗口函数
> Layer: L8 | OVER / PARTITION BY

```sql
SELECT
    mchnt_cd,
    trans_id,
    trans_at,
    trans_time,
    sum(trans_at) OVER (
        PARTITION BY mchnt_cd
        ORDER BY trans_time
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumsum_at,
    row_number() OVER (
        PARTITION BY mchnt_cd
        ORDER BY trans_time
    ) AS rn
FROM trans_flow
WHERE trans_status = 'SUCCESS'
```

### Q11 — CTE (WITH)
> Layer: (独立特性) | 公共表表达式

```sql
WITH succ_trans AS (
    SELECT
        mchnt_cd,
        trans_at,
        trans_time
    FROM trans_flow
    WHERE trans_status = 'SUCCESS'
      AND trans_time >= '2026-06-01 00:00:00'
),
mchnt_stats AS (
    SELECT
        mchnt_cd,
        sum(trans_at) AS total_at,
        count(1) AS cnt
    FROM succ_trans
    GROUP BY mchnt_cd
)
SELECT
    a.mchnt_cd,
    b.mchnt_nm,
    a.total_at,
    a.cnt,
    a.total_at / a.cnt AS avg_at
FROM mchnt_stats a
LEFT JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
```

### Q12 — sink表绑定
> Layer: L9 | 将查询结果写入 middle 表

```sql
INSERT INTO dws_mchnt_day
SELECT
    mchnt_cd,
    date_format(trans_time, 'yyyyMMdd') AS stat_date,
    count(1) AS trans_cnt,
    sum(trans_at) AS trans_at,
    sum(CASE WHEN trans_status = 'SUCCESS' THEN 1 ELSE 0 END) AS succ_cnt
FROM trans_flow
WHERE trans_time >= '2026-06-01 00:00:00'
GROUP BY mchnt_cd, date_format(trans_time, 'yyyyMMdd')
```

### Q13 — LATERAL VIEW
> Layer: (独立特性) | 行转列

```sql
SELECT
    mchnt_cd,
    mchnt_nm,
    industry_tag
FROM dim_mchnt
LATERAL VIEW explode(split(industry_list, ',')) t AS industry_tag
```
> `industry_list` 示例值: `'H001,H003,H007'`

### Q14 — 违规: LIMIT
> Layer: L10 | 应拒绝

```sql
SELECT * FROM trans_flow LIMIT 10
```

### Q15 — 违规: 3表 INNER JOIN
> Layer: L10 | 应拒绝

```sql
SELECT a.trans_id, b.mchnt_nm, c.industry_nm
FROM trans_flow a
INNER JOIN dim_mchnt b ON a.mchnt_cd = b.mchnt_cd
INNER JOIN dim_industry c ON b.industry_id = c.industry_id
```

---

## 3. 引擎 Layer 覆盖检查

| Layer | 特性 | 覆盖查询 |
|---|---|---|
| L0 | 单表无变换 | Q1 |
| L1 | 单表表达式 (nvl/round/concat) | Q5 |
| L2 | 单表聚合 / 两表 LEFT JOIN + 聚合 | Q1, Q2, Q6 |
| L3 | 3+表 LEFT JOIN | Q3 |
| L4 | 子查询 / 嵌套 | Q4, Q7 |
| L5 | JOIN (在L3已覆盖) | Q3 |
| L6 | CASE WHEN / 组合条件 | Q4, Q8 |
| L7 | UNION | Q9 |
| L8 | 窗口函数 | Q10 |
| L9 | sink表绑定 (middle) | Q12 |
| — | CTE | Q11 |
| — | LATERAL VIEW | Q13 (可选) |
| L10 | 违规拒绝 | Q14, Q15 |

---

## 4. 构建顺序建议

```
1. 建 root 表: trans_flow, dim_mchnt, dim_usr, dim_industry, dim_domin, usr_behave_bury
2. 运行 Q1-Q11, Q13-Q15 (不依赖 middle 表)
3. 建 middle 表: dws_mchnt_day (空表结构, source=middle)
4. 运行 Q12 (生成 dws_mchnt_day 的血缘数据)
5. 运行 Q9 (依赖 dws_mchnt_day)
```
