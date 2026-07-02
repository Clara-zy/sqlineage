-- SQLineage 数据库 Schema
-- PostgreSQL 15+

-- ============================================================
-- 1. 用户与会话
-- ============================================================

DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('admin', 'user', 'expert', 'guest');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(64) UNIQUE NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    role            user_role NOT NULL DEFAULT 'guest',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sessions (
    token       VARCHAR(256) PRIMARY KEY,
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

-- ============================================================
-- 2. 表结构
-- ============================================================

DO $$ BEGIN
    CREATE TYPE table_source AS ENUM ('root', 'middle');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS tables (
    id                  SERIAL PRIMARY KEY,
    schema_name         VARCHAR(128) NOT NULL,
    table_name          VARCHAR(128) NOT NULL,
    logical_name        VARCHAR(256),
    table_type          VARCHAR(64) NOT NULL,
    source              table_source NOT NULL,
    input_subquery_hash VARCHAR(16),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ,
    UNIQUE (schema_name, table_name)
);
CREATE INDEX IF NOT EXISTS idx_tables_source ON tables(source);

CREATE TABLE IF NOT EXISTS table_columns (
    id              SERIAL PRIMARY KEY,
    table_id        INT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    column_name     VARCHAR(128) NOT NULL,
    column_type     VARCHAR(64) NOT NULL,
    column_comment  VARCHAR(512),
    ordinal_position INT NOT NULL,
    field_hash      VARCHAR(16),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ,
    UNIQUE (table_id, column_name),
    UNIQUE (table_id, ordinal_position)
);
CREATE INDEX IF NOT EXISTS idx_tc_table ON table_columns(table_id);

-- ============================================================
-- 3. 版本追踪表
-- ============================================================

DO $$ BEGIN
    CREATE TYPE tracking_status AS ENUM ('active', 'deprecated');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS table_input_subqueries (
    id                  SERIAL PRIMARY KEY,
    table_id            INT NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    subquery_hash       VARCHAR(16) NOT NULL,
    status              tracking_status NOT NULL DEFAULT 'active',
    deprecated_at       TIMESTAMPTZ,
    deprecated_reason   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tis_table_status ON table_input_subqueries(table_id, status);

CREATE TABLE IF NOT EXISTS table_column_mappings (
    id                  SERIAL PRIMARY KEY,
    table_column_id     INT NOT NULL REFERENCES table_columns(id) ON DELETE CASCADE,
    field_hash          VARCHAR(16) NOT NULL,
    status              tracking_status NOT NULL DEFAULT 'active',
    deprecated_at       TIMESTAMPTZ,
    deprecated_reason   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tcm_col_status ON table_column_mappings(table_column_id, status);

-- ============================================================
-- 4. 字段公民 (DAG 节点)
-- ============================================================

DO $$ BEGIN
    CREATE TYPE field_type AS ENUM (
        'atomic', 'literal', 'expression',
        'atomic_condition', 'composite_condition'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS fields (
    hash        VARCHAR(16) PRIMARY KEY,
    field_type  field_type NOT NULL,
    expression  JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fields_field_type ON fields(field_type);
CREATE INDEX IF NOT EXISTS idx_fields_expression_gin ON fields USING GIN (expression);

-- ============================================================
-- 5. 字段引用关系 (DAG 边)
-- ============================================================

CREATE TABLE IF NOT EXISTS field_references (
    referenced_hash VARCHAR(16) NOT NULL REFERENCES fields(hash) ON DELETE CASCADE,
    referrer_hash   VARCHAR(16) NOT NULL REFERENCES fields(hash) ON DELETE CASCADE,
    PRIMARY KEY (referenced_hash, referrer_hash)
);
CREATE INDEX IF NOT EXISTS idx_fr_referrer ON field_references(referrer_hash);

-- ============================================================
-- 6. 子查询公民
-- ============================================================

DO $$ BEGIN
    CREATE TYPE subquery_type AS ENUM ('select', 'union');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS subqueries (
    hash            VARCHAR(16) PRIMARY KEY,
    subquery_type   subquery_type NOT NULL,
    structure       JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_subqueries_type ON subqueries(subquery_type);

-- ============================================================
-- 7. 自然语义
-- ============================================================

DO $$ BEGIN
    CREATE TYPE citizen_type AS ENUM ('field', 'table', 'subquery');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE semantics_source AS ENUM ('table_structure', 'ai_inferred', 'manual');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS natural_semantics (
    id                  SERIAL PRIMARY KEY,
    citizen_hash        VARCHAR(16) NOT NULL,
    citizen_type        citizen_type NOT NULL,
    semantics           TEXT NOT NULL,
    source              semantics_source NOT NULL,
    status              tracking_status NOT NULL DEFAULT 'active',
    deprecated_at       TIMESTAMPTZ,
    deprecated_reason   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ns_citizen ON natural_semantics(citizen_hash, citizen_type);

-- ============================================================
-- 8. 字段枚举值
-- ============================================================

CREATE TABLE IF NOT EXISTS field_enums (
    id                  SERIAL PRIMARY KEY,
    field_hash          VARCHAR(16) NOT NULL,
    enum_values         JSONB NOT NULL,
    source              VARCHAR(32) NOT NULL DEFAULT 'ddl_comment',
    status              tracking_status NOT NULL DEFAULT 'active',
    deprecated_at       TIMESTAMPTZ,
    deprecated_reason   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fe_hash_status ON field_enums(field_hash, status);

-- ============================================================
-- 9. 默认管理员
-- ============================================================

-- 密码 nbkknb 的 bcrypt hash
INSERT INTO users (username, password_hash, role)
VALUES ('admin', '$2b$12$SiTiOAn15US5UPHMWbDnkO2WZHPGDDPtSfqtPwZJq3huXB.lNBnuu', 'admin')
ON CONFLICT (username) DO NOTHING;
