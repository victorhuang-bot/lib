# V19.2.10 Gmail API — Concurrent PostgreSQL migration hotfix

基底：V19.2.9，保留全部 V19.2.9 功能。

## Render V19.2.9 失敗原因
兩個 Render service（Oregon / Singapore）共用同一個 Neon PostgreSQL，
且可能同時 Auto-Deploy 同一個 GitHub commit。

V19.2.9 在啟動 bootstrap 與 migration 都會執行 delivery_items 建表。
PostgreSQL 即使使用 CREATE TABLE IF NOT EXISTS，在兩個 transaction 同時建立
同名 relation/type 時仍可能發生：

psycopg.errors.UniqueViolation
duplicate key value violates unique constraint "pg_type_typname_nsp_index"
Key (typname, typnamespace)=(delivery_items, 2200) already exists.

## V19.2.10 修正
1. delivery_items 不再由通用 bootstrap executescript 建立。
2. delivery_items 改由 migrate_v1929 單一負責。
3. PostgreSQL 建表前使用 session advisory lock：
   pg_advisory_lock(19290)
4. 取得 lock 後再用 to_regclass('public.delivery_items') 檢查：
   - 已存在：直接跳過 CREATE TABLE
   - 不存在：才建立
5. finally 一定 pg_advisory_unlock(19290)。
6. DDL migration 暫時允許 60 秒 statement timeout。

## 保留 V19.2.9
- VOID 作廢配送
- 三種完成狀態分離
- 總館隨車物品
- 分館可調整實際簽收時間
- 公文送出 / 公文收回 / 圖書送出 / 圖書收回分工
- Gmail API
