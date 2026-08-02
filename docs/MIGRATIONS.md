# 数据库迁移 —— 单一 Source of Truth

## 结论

**Flyway（Java 侧）是 PostgreSQL schema 的唯一 source of truth。Python 不再管理迁移。**

- 迁移脚本：`shopmesh-java/src/main/resources/db/migration/V{n}__*.sql`
- Java 启动时由 Spring 自动执行（`spring.flyway.enabled=true`，`baseline-on-migrate=true`；JPA `ddl-auto=validate` 只校验、不建表）
- Python 侧通过 SQLModel (`src/db/models.py`) **读写既有 schema**，模型需与 Flyway 保持一致，但**不驱动任何迁移**

## 为什么（背景）

早期双语言架构下 schema 由**两套迁移工具**并行管理：Java 的 Flyway 和 Python 的 Alembic，二者写同一个库。项目最初的设计文档（`docs/superpowers/specs/2026-05-29-*`）其实已经定了「Flyway 唯一、删除 Alembic」，但落地时发生漂移——购物功能又用 Alembic 加了一版迁移（`a1c2e3f40501`：orders 的 `items_json` + `idempotency_key`），并用 `alembic stamp` 手工打标。

后果是**没人能说清 schema 到底由谁建**：

- 两套迁移史各自定义了同一批表（users/sessions/orders/llm_usage/... 都写了两遍）
- 实际运行的 infra 库里只有 `alembic_version`、没有 `flyway_schema_history` —— 说明它是走 Alembic/stamp 路径建的；而完整 `docker compose up`（含 java-api）却由 Flyway 建
- 「直接 ALTER 再 stamp」使 live schema 变成谁都无法从零复现的手工产物

这是**双 source of truth** 的典型隐患：两个环境（Java 引导 vs Python 引导）会悄悄长歪。

## 本次收敛做了什么

1. 把 Alembic 独有的改动（`a1c2e3f40501`）补成 Flyway **`V5__order_items_idempotency.sql`**，使 Flyway `V1–V5` 能从零重建**完整** live schema（已逐表比对确认一致）。V5 用幂等 DDL（`IF NOT EXISTS`），可安全套用到已存在这些列的现有库。
2. **删除 Alembic**：`alembic/`、`alembic.ini`、`scripts/migrate.py`、`requirements.txt` 的 `alembic` 依赖。运行期无任何代码调用 Alembic，故删除不影响任何服务。
3. 更新 `CLAUDE.md` 明确 Flyway 唯一所有权。

> 现有 dev 库里残留的 `alembic_version` 表是无害遗留物，Flyway 不理会它；全新 Flyway 建的库不会有它，可忽略或手工 `DROP TABLE alembic_version`。

## 怎么改表

新增一个 Flyway 迁移即可，例：

```sql
-- shopmesh-java/src/main/resources/db/migration/V6__add_foo.sql
ALTER TABLE orders ADD COLUMN IF NOT EXISTS foo VARCHAR;
```

下次 Java 启动时自动应用。同步更新 `src/db/models.py` 里对应的 SQLModel 字段。

## 本地开发怎么拿到 schema

schema 由 Flyway 在 **Java 服务启动时**建。

- **完整栈**：`docker compose up` → java-api 启动 → Flyway 自动迁移。
- **纯 Python 开发（只起 `docker-compose.infra.yml`）**：先跑一次完整栈（或单独起 java-api）让 Flyway 建好 schema，之后 infra 库的 Postgres 卷会持久化，再切回 infra-only 即可复用。**不要**再为 Python 单独维护一份建表脚本——那会重新引入第二个 source of truth。
