# V19.2.6 Gmail API — PostgreSQL startup migration timeout hotfix

基底：V19.2.5

## Render 失敗原因
啟動時執行：
`ALTER TABLE daily_routes ADD COLUMN IF NOT EXISTS secretary_signature TEXT`

但 PostgreSQL 連線一般查詢設定為 10 秒 statement_timeout，
Neon/Render 免費環境在 DDL 等待時超過 10 秒，
因此啟動被 `psycopg.errors.QueryCanceled` 中止。

## V19.2.6 修正
1. `migrate_route_secretary_signatures()` 先查 `information_schema.columns`。
2. 若欄位已存在，直接 return，不再每次啟動執行 ALTER TABLE。
3. 只有欄位真的缺少時才執行 DDL。
4. DDL migration 暫時使用：
   - statement_timeout = 60 秒
   - lock_timeout = 8 秒
5. 保持 migration idempotent，避免重複部署造成重複欄位。
6. 若另一個部署已同步完成欄位，會重新檢查 schema 後直接視為成功。

## 保留
- V19.2.5 待分館補簽不阻擋司機/秘書路線簽名
- Gmail API
- 月份路線統計
- 公文預填
- QR / 4 位碼
- 電子簽收
- Audit Log
- 報表
